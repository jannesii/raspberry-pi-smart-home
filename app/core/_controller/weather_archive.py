"""Permanent weather evidence: retrievals, original replies and revised readings."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, insert, select

if TYPE_CHECKING:
    from sqlalchemy import Engine

from ..schema import weather_observation, weather_reply, weather_retrieval

logger = logging.getLogger(__name__)


def _same(row, reading) -> bool:
    if row.status != reading.status:
        return False
    if reading.status == "invalid":
        return row.source_value == reading.source_value
    return row.value == reading.value


def _first_retrieval(conn, stmt) -> dict | None:
    row = conn.execute(stmt).mappings().first()
    return _retrieval(row) if row else None


def _retrieval(row) -> dict:
    values = dict(row)
    values["location"] = json.loads(values["location"]) if values["location"] else None
    values["summary"] = json.loads(values["summary"])
    return values


class WeatherArchiveMixin:
    _sa_engine: Engine

    def record_weather_observation_retrieval(
        self,
        retrieval: dict,
        content: bytes | None,
        series: list | None,
        *,
        requested: tuple[str, ...] = (),
        meta: dict[str, tuple[str | None, str | None]] | None = None,
    ) -> dict:
        """Persist one collection attempt atomically.

        A successful attempt stores its original reply (deduplicated by
        checksum) and any reading that is new or differs from the latest
        revision for the same station, field and source time. Unchanged
        readings add nothing; the retrieval row still records the receipt.
        """
        meta = meta or {}
        summary: dict[str, Any] = {}
        location = None
        if series is not None:
            by_param: dict[str, set[str]] = {}
            for entry in series:
                by_param.setdefault(entry.parameter, set()).update(r.status for r in entry.readings)
                if location is None and entry.location:
                    location = entry.location
            summary = {
                "series": len(series),
                "readings": sum(len(s.readings) for s in series),
                "missing": sum(1 for s in series for r in s.readings if r.status == "missing"),
                "invalid": sum(1 for s in series for r in s.readings if r.status == "invalid"),
                "fields_with_values": sorted(p for p, st in by_param.items() if "valid" in st),
                "fields_without_values": sorted(
                    p for p, st in by_param.items() if "valid" not in st
                ),
                "fields_absent": [p for p in requested if p not in by_param],
                # Unusable source times and NaN padding between hourly values.
                "skipped_points": sum(s.skipped for s in series),
                "request": retrieval.get("request"),
            }
        elif retrieval.get("request"):
            summary = {"request": retrieval["request"]}
        with self._sa_engine.begin() as conn:
            reply_id = None
            if content is not None:
                reply_id = self._store_weather_reply(conn, content, retrieval["retrieved_at"])
            retrieval_id = conn.execute(
                insert(weather_retrieval)
                .values(
                    kind=retrieval["kind"],
                    source_id=retrieval["source_id"],
                    intended_at=retrieval["intended_at"],
                    requested_start=retrieval["requested_start"],
                    requested_end=retrieval["requested_end"],
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
            new = revised = unchanged = 0
            for entry in series or []:
                fmisid = entry.fmisid or retrieval["source_id"]
                if not entry.readings:
                    continue
                times = [r.observed_at for r in entry.readings]
                latest = self._latest_weather_revisions(conn, fmisid, entry.parameter, times)
                unit, period = meta.get(entry.parameter, (None, None))
                rows = []
                for reading in entry.readings:
                    previous = latest.get(reading.observed_at)
                    if previous is not None and _same(previous, reading):
                        unchanged += 1
                        continue
                    revision = 1 if previous is None else previous.revision + 1
                    if previous is None:
                        new += 1
                    else:
                        revised += 1
                    rows.append(
                        {
                            "fmisid": fmisid,
                            "parameter": entry.parameter,
                            "observed_at": reading.observed_at,
                            "revision": revision,
                            "status": reading.status,
                            "value": reading.value,
                            "source_value": reading.source_value,
                            "unit": unit,
                            "period": period,
                            "property_href": entry.property_href,
                            "retrieval_id": retrieval_id,
                        }
                    )
                if rows:
                    conn.execute(insert(weather_observation), rows)
            if series is not None:
                summary.update(new=new, revised=revised, unchanged=unchanged)
            conn.execute(
                weather_retrieval.update()
                .where(weather_retrieval.c.id == retrieval_id)
                .values(summary=json.dumps(summary))
            )
        if revised:
            logger.info(
                "Weather source revised %d archived readings station=%s retrieval=%s",
                revised,
                retrieval["source_id"],
                retrieval_id,
            )
        return {"id": retrieval_id, "reply_id": reply_id, "summary": summary}

    @staticmethod
    def _store_weather_reply(conn, content: bytes, retrieved_at: str) -> int:
        digest = hashlib.sha256(content).hexdigest()
        existing = conn.scalar(select(weather_reply.c.id).where(weather_reply.c.sha256 == digest))
        if existing is not None:
            return existing
        # One collector thread writes replies, so select-then-insert cannot race.
        return conn.execute(
            insert(weather_reply)
            .values(
                sha256=digest,
                byte_size=len(content),
                content_type="application/xml",
                compression="gzip",
                content=gzip.compress(content, mtime=0),
                first_retrieved_at=retrieved_at,
            )
            .returning(weather_reply.c.id)
        ).scalar_one()

    @staticmethod
    def _latest_weather_revisions(conn, fmisid: str, parameter: str, times: list[str]) -> dict:
        obs = weather_observation
        newest = (
            select(obs.c.observed_at, func.max(obs.c.revision).label("revision"))
            .where(obs.c.fmisid == fmisid, obs.c.parameter == parameter)
            .where(obs.c.observed_at >= min(times), obs.c.observed_at <= max(times))
            .group_by(obs.c.observed_at)
            .subquery()
        )
        stmt = select(obs).join(
            newest,
            and_(
                obs.c.observed_at == newest.c.observed_at,
                obs.c.revision == newest.c.revision,
            ),
        )
        stmt = stmt.where(obs.c.fmisid == fmisid, obs.c.parameter == parameter)
        return {row.observed_at: row for row in conn.execute(stmt)}

    def list_weather_observations(
        self, start_iso: str, end_iso: str, *, fmisid: str | None = None
    ) -> list[dict]:
        """Every archived revision with source time in [start, end], oldest first."""
        obs, ret = weather_observation, weather_retrieval
        stmt = (
            select(
                obs,
                ret.c.retrieved_at,
                ret.c.reply_id,
                ret.c.location,
            )
            .join(ret, ret.c.id == obs.c.retrieval_id)
            .where(obs.c.observed_at >= start_iso, obs.c.observed_at <= end_iso)
            .order_by(obs.c.fmisid, obs.c.parameter, obs.c.observed_at, obs.c.revision)
        )
        if fmisid:
            stmt = stmt.where(obs.c.fmisid == fmisid)
        with self._sa_engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(stmt).mappings()]
        names: dict[str, str | None] = {}
        latest: dict[tuple, int] = {}
        for row in rows:
            location = row.pop("location")
            names.setdefault(row["fmisid"], None)
            if location and names[row["fmisid"]] is None:
                names[row["fmisid"]] = json.loads(location).get("name")
            key = (row["fmisid"], row["parameter"], row["observed_at"])
            latest[key] = max(latest.get(key, 0), row["revision"])
        for row in rows:
            row["station_name"] = names[row["fmisid"]]
            key = (row["fmisid"], row["parameter"], row["observed_at"])
            row["current"] = row["revision"] == latest[key]
        return rows

    def list_weather_retrievals(
        self, start_iso: str, end_iso: str, *, kind: str = "observation"
    ) -> list[dict]:
        ret = weather_retrieval
        stmt = (
            select(weather_retrieval)
            .where(ret.c.kind == kind)
            .where(ret.c.retrieved_at >= start_iso, ret.c.retrieved_at <= end_iso)
            .order_by(ret.c.retrieved_at, ret.c.id)
        )
        with self._sa_engine.connect() as conn:
            return [_retrieval(row) for row in conn.execute(stmt).mappings()]

    def get_weather_collection_health(self, kind: str = "observation") -> dict[str, dict]:
        """Per source: latest attempt, latest success and latest failure."""
        ret = weather_retrieval
        health: dict[str, dict] = {}
        with self._sa_engine.connect() as conn:
            sources = conn.execute(
                select(ret.c.source_id).where(ret.c.kind == kind).distinct()
            ).scalars()
            for source in sorted(sources):
                base = select(weather_retrieval).where(
                    ret.c.kind == kind, ret.c.source_id == source
                )
                newest = base.order_by(ret.c.retrieved_at.desc(), ret.c.id.desc()).limit(1)
                last = _first_retrieval(conn, newest)
                success = _first_retrieval(conn, newest.where(ret.c.status == "ok"))
                failure = _first_retrieval(conn, newest.where(ret.c.status != "ok"))
                after = success["retrieved_at"] if success else ""
                failures = conn.scalar(
                    select(func.count())
                    .select_from(ret)
                    .where(
                        ret.c.kind == kind,
                        ret.c.source_id == source,
                        ret.c.status != "ok",
                        ret.c.retrieved_at > after,
                    )
                )
                location = next(
                    (r["location"] for r in (success, last) if r and r["location"]), None
                )
                health[source] = {
                    "source_id": source,
                    "station_name": (location or {}).get("name"),
                    "last_attempt": last,
                    "last_success": success,
                    "last_failure": failure,
                    "consecutive_failures": failures,
                }
        return health

    def get_weather_reply(self, reply_id: int) -> dict | None:
        """Original reply content, verified against its stored checksum."""
        with self._sa_engine.connect() as conn:
            row = (
                conn.execute(select(weather_reply).where(weather_reply.c.id == reply_id))
                .mappings()
                .first()
            )
        if row is None:
            return None
        content = gzip.decompress(bytes(row["content"]))
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            logger.error("Weather reply %s failed its integrity check", reply_id)
            raise ValueError("Stored weather reply failed its integrity check")
        return {
            "id": row["id"],
            "sha256": row["sha256"],
            "content_type": row["content_type"],
            "first_retrieved_at": row["first_retrieved_at"],
            "content": content,
        }
