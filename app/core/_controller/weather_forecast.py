"""Permanent point-forecast evidence: received versions, values and receipts."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, insert, select

if TYPE_CHECKING:
    from sqlalchemy import Engine

from ...services.weather.forecast_archive import ForecastPoint
from ..schema import (
    weather_forecast_content,
    weather_forecast_receipt,
    weather_forecast_value,
    weather_forecast_version,
    weather_retrieval,
)
from .weather_archive import WeatherArchiveMixin

logger = logging.getLogger(__name__)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _groups(series: list) -> dict[tuple, list]:
    """Series sharing model, source times and returned location form one version."""
    groups: dict[tuple, list] = {}
    for entry in series:
        key = (
            entry.procedure,
            entry.analysis_time,
            entry.result_time,
            _canonical(entry.location) if entry.location else None,
        )
        groups.setdefault(key, []).append(entry)
    return groups


def _value_rows(entries: list, meta: dict) -> list[dict]:
    rows = []
    for entry in entries:
        unit, period, statistic = meta.get(entry.parameter, (None, None, None))
        for value in entry.values:
            rows.append(
                {
                    "parameter": entry.parameter,
                    "valid_at": value.valid_at,
                    "status": value.status,
                    "value": value.value,
                    "source_value": value.source_value,
                    "unit": unit,
                    "period": period,
                    "statistic": statistic,
                    "property_href": entry.property_href,
                }
            )
    rows.sort(key=lambda r: (r["parameter"], r["valid_at"]))
    return rows


def _content_digest(rows: list[dict]) -> str:
    # Source text, not the parsed float, defines unchanged numerical content.
    keys = ("parameter", "valid_at", "status", "source_value", "unit", "period", "statistic")
    material = [[row[k] for k in (*keys, "property_href")] for row in rows]
    return hashlib.sha256(_canonical(material).encode()).hexdigest()


def _point_dict(text: str | None) -> dict | None:
    point = ForecastPoint.from_id(text)
    return point.as_dict() if point else None


class WeatherForecastMixin:
    _sa_engine: Engine

    def record_weather_forecast_retrieval(
        self,
        retrieval: dict,
        content: bytes | None,
        series: list | None,
        *,
        requested: tuple[str, ...] = (),
        meta: dict[str, tuple[str | None, str | None, str | None]] | None = None,
    ) -> dict:
        """Persist one forecast check atomically.

        Each distinct (request, model, origin, result time, returned location,
        numerical content) is one version. Receiving it again only adds a
        receipt for this retrieval. Changed content or result time for an origin
        already held is the next revision; earlier versions are never altered.
        """
        meta = meta or {}
        summary: dict[str, Any] = {}
        if retrieval.get("request"):
            summary["request"] = retrieval["request"]
        location = None
        if series is not None:
            by_param: dict[str, set[str]] = {}
            for entry in series:
                by_param.setdefault(entry.parameter, set()).update(v.status for v in entry.values)
                if location is None and entry.location:
                    location = entry.location
            times = [v.valid_at for s in series for v in s.values]
            summary.update(
                series=len(series),
                values=len(times),
                missing=sum(1 for s in series for v in s.values if v.status == "missing"),
                invalid=sum(1 for s in series for v in s.values if v.status == "invalid"),
                fields_with_values=sorted(p for p, st in by_param.items() if "valid" in st),
                fields_without_values=sorted(p for p, st in by_param.items() if "valid" not in st),
                fields_absent=[p for p in requested if p not in by_param],
                skipped_points=sum(s.skipped for s in series),
                # NaN filler FMI returns for requested hours outside the run.
                outside_run_points=sum(s.outside_run for s in series),
                valid_start=min(times, default=None),
                valid_end=max(times, default=None),
            )
        request_text = _canonical(retrieval.get("request") or {})
        received: list[dict] = []
        with self._sa_engine.begin() as conn:
            reply_id = None
            if content is not None:
                reply_id = WeatherArchiveMixin._store_weather_reply(
                    conn, content, retrieval["retrieved_at"]
                )
            retrieval_id = conn.execute(
                insert(weather_retrieval)
                .values(
                    kind=retrieval["kind"],
                    source_id=retrieval["source_id"],
                    intended_at=retrieval["intended_at"],
                    requested_start=retrieval.get("requested_start"),
                    requested_end=retrieval.get("requested_end"),
                    started_at=retrieval["started_at"],
                    retrieved_at=retrieval["retrieved_at"],
                    status=retrieval["status"],
                    http_status=retrieval.get("http_status"),
                    error=retrieval.get("error"),
                    reply_id=reply_id,
                    location=json.dumps(location) if location else None,
                    summary="{}",
                )
                .returning(weather_retrieval.c.id)
            ).scalar_one()
            for key, entries in _groups(series or []).items():
                rows = _value_rows(entries, meta)
                if not rows:
                    continue
                received.append(
                    self._receive_forecast_version(
                        conn, retrieval, retrieval_id, request_text, key, entries, rows
                    )
                )
            if series is not None:
                summary.update(
                    versions=received,
                    new_versions=sum(1 for v in received if v["new"]),
                    repeated_versions=sum(1 for v in received if not v["new"]),
                )
            conn.execute(
                weather_retrieval.update()
                .where(weather_retrieval.c.id == retrieval_id)
                .values(summary=json.dumps(summary))
            )
        for version in received:
            if version["new"] and version["revision"] > 1:
                logger.info(
                    "Weather forecast revised origin=%s revision=%d point=%s retrieval=%s",
                    version["analysis_time"],
                    version["revision"],
                    retrieval["source_id"],
                    retrieval_id,
                )
        return {"id": retrieval_id, "reply_id": reply_id, "summary": summary}

    @staticmethod
    def _receive_forecast_version(
        conn, retrieval, retrieval_id, request_text, key, entries, rows
    ) -> dict:
        procedure, analysis_time, result_time, location_text = key
        digest = _content_digest(rows)
        content_id = conn.scalar(
            select(weather_forecast_content.c.id).where(weather_forecast_content.c.sha256 == digest)
        )
        if content_id is None:
            # One collector thread writes forecasts, so select-then-insert cannot race.
            content_id = conn.execute(
                insert(weather_forecast_content)
                .values(sha256=digest, value_count=len(rows), first_retrieval_id=retrieval_id)
                .returning(weather_forecast_content.c.id)
            ).scalar_one()
            conn.execute(
                insert(weather_forecast_value), [{**row, "content_id": content_id} for row in rows]
            )
        ver = weather_forecast_version
        same_origin = (
            ver.c.source_id == retrieval["source_id"],
            ver.c.request == request_text,
            ver.c.procedure.is_not_distinct_from(procedure),
            ver.c.analysis_time.is_not_distinct_from(analysis_time),
        )
        # Only the latest revision of an origin can be received again; content
        # reverting to an earlier revision is newer information, so it becomes
        # the next revision (its values are still stored once).
        latest = (
            conn.execute(
                select(
                    ver.c.id, ver.c.revision, ver.c.result_time, ver.c.location, ver.c.content_id
                )
                .where(*same_origin)
                .order_by(ver.c.revision.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        new = latest is None or (
            latest["result_time"],
            latest["location"],
            latest["content_id"],
        ) != (result_time, location_text, content_id)
        if new:
            revision = (latest["revision"] if latest else 0) + 1
            starts = [e.phenomenon_start for e in entries if e.phenomenon_start]
            ends = [e.phenomenon_end for e in entries if e.phenomenon_end]
            version_id = conn.execute(
                insert(ver)
                .values(
                    source_id=retrieval["source_id"],
                    request=request_text,
                    procedure=procedure,
                    analysis_time=analysis_time,
                    result_time=result_time,
                    phenomenon_start=min(starts, default=None),
                    phenomenon_end=max(ends, default=None),
                    location=location_text,
                    content_id=content_id,
                    revision=revision,
                    first_retrieval_id=retrieval_id,
                    first_retrieved_at=retrieval["retrieved_at"],
                )
                .returning(ver.c.id)
            ).scalar_one()
        else:
            version_id, revision = latest["id"], latest["revision"]
        conn.execute(
            insert(weather_forecast_receipt).values(
                retrieval_id=retrieval_id, version_id=version_id
            )
        )
        return {
            "id": version_id,
            "analysis_time": analysis_time,
            "result_time": result_time,
            "revision": revision,
            "new": new,
        }

    def list_weather_forecast_versions(
        self, start_iso: str, end_iso: str, *, source_id: str | None = None
    ) -> list[dict]:
        """Versions received in [start, end], in the order first received.

        Values are not included; ``get_weather_forecast_version`` returns them.
        """
        ver, rec, ret = weather_forecast_version, weather_forecast_receipt, weather_retrieval
        ids = (
            select(rec.c.version_id)
            .join(ret, ret.c.id == rec.c.retrieval_id)
            .where(ret.c.retrieved_at >= start_iso, ret.c.retrieved_at <= end_iso)
        )
        stmt = select(ver).where(ver.c.id.in_(ids)).order_by(ver.c.first_retrieved_at, ver.c.id)
        if source_id:
            stmt = stmt.where(ver.c.source_id == source_id)
        with self._sa_engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(stmt).mappings()]
            return self._describe_forecast_versions(conn, rows)

    def get_weather_forecast_version(self, version_id: int) -> dict | None:
        """One received version with its complete returned horizon and receipts."""
        ver, val = weather_forecast_version, weather_forecast_value
        with self._sa_engine.connect() as conn:
            row = conn.execute(select(ver).where(ver.c.id == version_id)).mappings().first()
            if row is None:
                return None
            [version] = self._describe_forecast_versions(conn, [dict(row)])
            values = conn.execute(
                select(val)
                .where(val.c.content_id == row["content_id"])
                .order_by(val.c.parameter, val.c.valid_at)
            ).mappings()
            version["values"] = [
                {k: v for k, v in value.items() if k not in ("id", "content_id")}
                for value in values
            ]
        return version

    def list_weather_forecast_unobserved(
        self, source_id: str, start_iso: str, end_iso: str, *, max_gap_s: float
    ) -> list[dict]:
        """Stretches of [start, end] with no successful check for over ``max_gap_s``.

        Built from retrieval history only: a stretch begins at the last
        successful check (or the range start) and ends at the next one (or the
        range end). Versions published and replaced inside it were never
        received; ``failed_attempts`` counts checks that did not succeed.
        """
        ret = weather_retrieval
        base = select(ret.c.retrieved_at).where(
            ret.c.kind == "forecast", ret.c.source_id == source_id
        )
        ok = base.where(ret.c.status == "ok")
        with self._sa_engine.connect() as conn:
            before = conn.scalar(
                ok.where(ret.c.retrieved_at <= start_iso)
                .order_by(ret.c.retrieved_at.desc())
                .limit(1)
            )
            inside = conn.execute(
                ok.where(ret.c.retrieved_at > start_iso, ret.c.retrieved_at <= end_iso).order_by(
                    ret.c.retrieved_at
                )
            ).scalars()
            successes = list(inside)
            failures = list(
                conn.execute(
                    base.where(
                        ret.c.status != "ok",
                        ret.c.retrieved_at >= start_iso,
                        ret.c.retrieved_at <= end_iso,
                    )
                ).scalars()
            )
        start, end = datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso)
        limit = timedelta(seconds=max_gap_s)
        previous = datetime.fromisoformat(before) if before else None
        gaps = []
        for boundary in [*(datetime.fromisoformat(t) for t in successes), end]:
            if previous is None or boundary - previous > limit:
                gap_start = max(previous, start) if previous else start
                if boundary > gap_start:
                    lo, hi = gap_start.isoformat(), boundary.isoformat()
                    gaps.append(
                        {
                            "start": lo,
                            "end": hi,
                            "failed_attempts": sum(
                                1
                                for t in failures
                                if gap_start < datetime.fromisoformat(t) < boundary
                            ),
                        }
                    )
            previous = boundary
        return gaps

    @staticmethod
    def _describe_forecast_versions(conn, rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        ver, val, rec = weather_forecast_version, weather_forecast_value, weather_forecast_receipt
        ret = weather_retrieval
        contents = {r["content_id"] for r in rows}
        spans = {
            r.content_id: r
            for r in conn.execute(
                select(
                    val.c.content_id,
                    func.count().label("value_count"),
                    func.min(val.c.valid_at).label("valid_start"),
                    func.max(val.c.valid_at).label("valid_end"),
                )
                .where(val.c.content_id.in_(contents))
                .group_by(val.c.content_id)
            )
        }
        statuses: dict[int, dict[str, set[str]]] = {}
        for r in conn.execute(
            select(val.c.content_id, val.c.parameter, val.c.status)
            .where(val.c.content_id.in_(contents))
            .distinct()
        ):
            statuses.setdefault(r.content_id, {}).setdefault(r.parameter, set()).add(r.status)
        receipts: dict[int, list[dict]] = {}
        for r in conn.execute(
            select(rec.c.version_id, ret.c.id, ret.c.retrieved_at, ret.c.reply_id)
            .join(ret, ret.c.id == rec.c.retrieval_id)
            .where(rec.c.version_id.in_([r["id"] for r in rows]))
            .order_by(ret.c.retrieved_at, ret.c.id)
        ):
            receipts.setdefault(r.version_id, []).append(
                {"retrieval_id": r.id, "retrieved_at": r.retrieved_at, "reply_id": r.reply_id}
            )
        latest: dict[tuple, int] = {}
        for row in rows:
            key = (row["source_id"], row["request"], row["procedure"], row["analysis_time"])
            if key not in latest:
                latest[key] = conn.scalar(
                    select(func.max(ver.c.revision)).where(
                        ver.c.source_id == row["source_id"],
                        ver.c.request == row["request"],
                        ver.c.procedure.is_not_distinct_from(row["procedure"]),
                        ver.c.analysis_time.is_not_distinct_from(row["analysis_time"]),
                    )
                )
        described = []
        for row in rows:
            request = json.loads(row["request"])
            span = spans.get(row["content_id"])
            fields = statuses.get(row["content_id"], {})
            requested = (request.get("parameters") or "").split(",")
            key = (row["source_id"], row["request"], row["procedure"], row["analysis_time"])
            described.append(
                {
                    "id": row["id"],
                    "source_id": row["source_id"],
                    "request": request,
                    "requested_location": _point_dict(request.get("latlon")),
                    "location": json.loads(row["location"]) if row["location"] else None,
                    "procedure": row["procedure"],
                    "analysis_time": row["analysis_time"],
                    "result_time": row["result_time"],
                    "phenomenon_start": row["phenomenon_start"],
                    "phenomenon_end": row["phenomenon_end"],
                    "valid_start": span.valid_start if span else None,
                    "valid_end": span.valid_end if span else None,
                    "value_count": span.value_count if span else 0,
                    "fields_with_values": sorted(p for p, st in fields.items() if "valid" in st),
                    "fields_without_values": sorted(
                        p for p, st in fields.items() if "valid" not in st
                    ),
                    "fields_absent": [p for p in requested if p and p not in fields],
                    "revision": row["revision"],
                    "current": row["revision"] == latest[key],
                    "first_retrieval_id": row["first_retrieval_id"],
                    "first_retrieved_at": row["first_retrieved_at"],
                    "receipts": receipts.get(row["id"], []),
                }
            )
        return described
