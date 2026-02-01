from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Engine, and_, func, insert, outerjoin, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import (
    CarHeaterKFactorActiveParams,
    CarHeaterKFactorBucketParams,
    CarHeaterKFactorConfig,
    CarHeaterKFactorCooldown,
    CarHeaterKFactorPredictionOutcome,
    CarHeaterKFactorResult,
    CarHeaterKFactorSession,
)
from ..schema import (
    car_heater_kfactor_active_params,
    car_heater_kfactor_bucket_params,
    car_heater_kfactor_config,
    car_heater_kfactor_cooldown,
    car_heater_kfactor_prediction_outcome,
    car_heater_kfactor_result,
    car_heater_kfactor_session,
)

logger = logging.getLogger(__name__)

_WIND_BUCKET_UNKNOWN = -999


def _encode_wind_bucket(wind_bucket: int | None) -> int:
    return _WIND_BUCKET_UNKNOWN if wind_bucket is None else int(wind_bucket)


def _decode_wind_bucket(wind_bucket: int | None) -> int | None:
    if wind_bucket is None:
        return None
    return None if int(wind_bucket) == _WIND_BUCKET_UNKNOWN else int(wind_bucket)


class CarHeaterKFactorMixin:
    # --- KFactor calibration sessions/results ---
    def record_kfactor_session(
        self,
        session: CarHeaterKFactorSession,
    ) -> CarHeaterKFactorSession:
        """Insert a new kfactor session row and return it with id set."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("record_kfactor_session: start_ts=%s", session.start_ts)

        try:
            stmt = (
                insert(car_heater_kfactor_session)
                .values(
                    start_ts=session.start_ts,
                    end_ts=session.end_ts,
                    auto_window_date=session.auto_window_date,
                    auto_window_start=session.auto_window_start,
                    auto_window_stop=session.auto_window_stop,
                    heater_mode=session.heater_mode,
                    outside_t_mean=session.outside_t_mean,
                    outside_t_min=session.outside_t_min,
                    outside_t_max=session.outside_t_max,
                    wind_mean=session.wind_mean,
                    cabin_t_start=session.cabin_t_start,
                    cabin_t_end=session.cabin_t_end,
                    cabin_t_max=session.cabin_t_max,
                    duration_s=session.duration_s,
                    sample_count=session.sample_count,
                    flags_json=session.flags_json,
                    quality_score=session.quality_score,
                    accepted=session.accepted,
                )
                .returning(car_heater_kfactor_session.c.id)
            )

            with sa_engine.begin() as conn:
                result = conn.execute(stmt)
                new_id = result.scalar_one()

            return CarHeaterKFactorSession(
                id=new_id,
                start_ts=session.start_ts,
                end_ts=session.end_ts,
                auto_window_date=session.auto_window_date,
                auto_window_start=session.auto_window_start,
                auto_window_stop=session.auto_window_stop,
                heater_mode=session.heater_mode,
                outside_t_mean=session.outside_t_mean,
                outside_t_min=session.outside_t_min,
                outside_t_max=session.outside_t_max,
                wind_mean=session.wind_mean,
                cabin_t_start=session.cabin_t_start,
                cabin_t_end=session.cabin_t_end,
                cabin_t_max=session.cabin_t_max,
                duration_s=session.duration_s,
                sample_count=session.sample_count,
                flags_json=session.flags_json,
                quality_score=session.quality_score,
                accepted=session.accepted,
            )
        except Exception as e:
            logger.exception("Error recording kfactor session: %s", e)
            raise

    def record_kfactor_result(
        self,
        result: CarHeaterKFactorResult,
    ) -> CarHeaterKFactorResult:
        """Insert a new kfactor result row and return it with id set."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("record_kfactor_result: session_id=%s", result.session_id)

        try:
            stmt = (
                insert(car_heater_kfactor_result)
                .values(
                    session_id=result.session_id,
                    model_version=result.model_version,
                    k_loss_W_per_K=result.k_loss_W_per_K,
                    eta=result.eta,
                    rmse_C=result.rmse_C,
                    r2=result.r2,
                    confidence=result.confidence,
                    promoted=result.promoted,
                    created_ts=result.created_ts,
                )
                .returning(car_heater_kfactor_result.c.id)
            )

            with sa_engine.begin() as conn:
                res = conn.execute(stmt)
                new_id = res.scalar_one()

            return CarHeaterKFactorResult(
                id=new_id,
                session_id=result.session_id,
                model_version=result.model_version,
                k_loss_W_per_K=result.k_loss_W_per_K,
                eta=result.eta,
                rmse_C=result.rmse_C,
                r2=result.r2,
                confidence=result.confidence,
                promoted=result.promoted,
                created_ts=result.created_ts,
            )
        except Exception as e:
            logger.exception("Error recording kfactor result: %s", e)
            raise

    def record_kfactor_prediction_outcome(
        self,
        outcome: CarHeaterKFactorPredictionOutcome,
    ) -> None:
        """Insert a new kfactor prediction outcome row."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug(
            "record_kfactor_prediction_outcome: predicted=%s, actual=%s",
            outcome.predicted_minutes,
            outcome.actual_minutes,
        )

        try:
            stmt = insert(car_heater_kfactor_prediction_outcome).values(
                predicted_minutes=outcome.predicted_minutes,
                actual_minutes=outcome.actual_minutes,
                error_minutes=outcome.error_minutes,
                cabin_start_c=outcome.cabin_start_c,
                cabin_end_c=outcome.cabin_end_c,
                target_c=outcome.target_c,
                outside_c=outcome.outside_c,
                created_ts=outcome.created_ts,
            )

            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error recording kfactor prediction outcome: %s", e)
            raise

    # --- Active params (single-row settings) ---
    def get_kfactor_active_params(self) -> CarHeaterKFactorActiveParams | None:
        """Load active kfactor params from the database, or return None if not set."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(car_heater_kfactor_active_params).where(
                car_heater_kfactor_active_params.c.id == 1
            )

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return None

            return CarHeaterKFactorActiveParams(
                id=1,
                k_loss_W_per_K=row["k_loss_W_per_K"],
                eta=row["eta"],
                updated_ts=row["updated_ts"],
                source=row["source"],
            )
        except Exception as e:
            logger.exception("Error getting kfactor active params: %s", e)
            raise

    def save_kfactor_active_params(self, params: CarHeaterKFactorActiveParams) -> None:
        """Persist active kfactor params to the database."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug(
            "save_kfactor_active_params: k_loss=%s, eta=%s", params.k_loss_W_per_K, params.eta
        )

        try:
            stmt = pg_insert(car_heater_kfactor_active_params).values(
                id=1,
                k_loss_W_per_K=params.k_loss_W_per_K,
                eta=params.eta,
                updated_ts=params.updated_ts,
                source=params.source,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "k_loss_W_per_K": stmt.excluded.k_loss_W_per_K,
                    "eta": stmt.excluded.eta,
                    "updated_ts": stmt.excluded.updated_ts,
                    "source": stmt.excluded.source,
                },
            )

            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving kfactor active params: %s", e)
            raise

    # --- Bucketed params (per outside temp + wind bucket) ---
    def get_kfactor_bucket_params(
        self,
        *,
        t_bucket: int,
        wind_bucket: int | None,
    ) -> CarHeaterKFactorBucketParams | None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        wind_bucket_db = _encode_wind_bucket(wind_bucket)

        try:
            # Custom ordering: exact wind_bucket match first, then fallback
            stmt = (
                select(car_heater_kfactor_bucket_params)
                .where(car_heater_kfactor_bucket_params.c.t_bucket == t_bucket)
                .where(car_heater_kfactor_bucket_params.c.wind_bucket == wind_bucket_db)
                .order_by(
                    car_heater_kfactor_bucket_params.c.updated_ts.desc(),
                    car_heater_kfactor_bucket_params.c.id.desc(),
                )
                .limit(1)
            )

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return None

            return CarHeaterKFactorBucketParams(
                id=row["id"],
                t_bucket=row["t_bucket"],
                wind_bucket=_decode_wind_bucket(row["wind_bucket"]),
                k_loss_W_per_K=row["k_loss_W_per_K"],
                eta=row["eta"],
                updated_ts=row["updated_ts"],
                source=row["source"],
            )
        except Exception as e:
            logger.exception("Error getting kfactor bucket params: %s", e)
            raise

    def get_kfactor_bucket_params_any_wind(
        self,
        *,
        t_bucket: int,
    ) -> CarHeaterKFactorBucketParams | None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = (
                select(car_heater_kfactor_bucket_params)
                .where(car_heater_kfactor_bucket_params.c.t_bucket == t_bucket)
                .order_by(
                    car_heater_kfactor_bucket_params.c.updated_ts.desc(),
                    car_heater_kfactor_bucket_params.c.id.desc(),
                )
                .limit(1)
            )

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return None

            return CarHeaterKFactorBucketParams(
                id=row["id"],
                t_bucket=row["t_bucket"],
                wind_bucket=_decode_wind_bucket(row["wind_bucket"]),
                k_loss_W_per_K=row["k_loss_W_per_K"],
                eta=row["eta"],
                updated_ts=row["updated_ts"],
                source=row["source"],
            )
        except Exception as e:
            logger.exception("Error getting kfactor bucket params any wind: %s", e)
            raise

    def save_kfactor_bucket_params(
        self,
        params: CarHeaterKFactorBucketParams,
    ) -> None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        wind_bucket_db = _encode_wind_bucket(params.wind_bucket)

        logger.debug(
            "save_kfactor_bucket_params: t=%s, wind=%s",
            params.t_bucket,
            params.wind_bucket,
        )

        try:
            stmt = pg_insert(car_heater_kfactor_bucket_params).values(
                t_bucket=params.t_bucket,
                wind_bucket=wind_bucket_db,
                k_loss_W_per_K=params.k_loss_W_per_K,
                eta=params.eta,
                updated_ts=params.updated_ts,
                source=params.source,
            )
            # Use index_elements instead of constraint name for SQLite compatibility
            stmt = stmt.on_conflict_do_update(
                index_elements=["t_bucket", "wind_bucket"],
                set_={
                    "k_loss_W_per_K": stmt.excluded.k_loss_W_per_K,
                    "eta": stmt.excluded.eta,
                    "updated_ts": stmt.excluded.updated_ts,
                    "source": stmt.excluded.source,
                },
            )

            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving kfactor bucket params: %s", e)
            raise

    # --- KFactor config (single-row settings) ---
    def get_kfactor_config(self) -> CarHeaterKFactorConfig | None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_kfactor_config called")

        try:
            stmt = select(car_heater_kfactor_config).where(car_heater_kfactor_config.c.id == 1)

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("get_kfactor_config: no row found")
                return None

            return CarHeaterKFactorConfig(
                id=1,
                config_json=row["config_json"],
                updated_ts=row["updated_ts"],
            )
        except Exception as e:
            logger.exception("Error getting kfactor config: %s", e)
            raise

    def save_kfactor_config(self, config: CarHeaterKFactorConfig) -> None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("save_kfactor_config called")

        try:
            stmt = pg_insert(car_heater_kfactor_config).values(
                id=1,
                config_json=config.config_json,
                updated_ts=config.updated_ts,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "config_json": stmt.excluded.config_json,
                    "updated_ts": stmt.excluded.updated_ts,
                },
            )

            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving kfactor config: %s", e)
            raise

    # --- KFactor cooldown (single-row settings) ---
    def get_kfactor_cooldown(self) -> CarHeaterKFactorCooldown | None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_kfactor_cooldown called")

        try:
            stmt = select(car_heater_kfactor_cooldown).where(car_heater_kfactor_cooldown.c.id == 1)

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("get_kfactor_cooldown: no row found")
                return None

            return CarHeaterKFactorCooldown(
                id=1,
                cooldown_until=row["cooldown_until"],
                updated_ts=row["updated_ts"],
            )
        except Exception as e:
            logger.exception("Error getting kfactor cooldown: %s", e)
            raise

    def save_kfactor_cooldown(self, cooldown: CarHeaterKFactorCooldown) -> None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("save_kfactor_cooldown called")

        try:
            stmt = pg_insert(car_heater_kfactor_cooldown).values(
                id=1,
                cooldown_until=cooldown.cooldown_until,
                updated_ts=cooldown.updated_ts,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "cooldown_until": stmt.excluded.cooldown_until,
                    "updated_ts": stmt.excluded.updated_ts,
                },
            )

            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving kfactor cooldown: %s", e)
            raise

    # --- Debug / reporting helpers ---
    def get_recent_kfactor_sessions(
        self,
        *,
        limit: int = 50,
        accepted_only: bool = False,
    ) -> list[CarHeaterKFactorSession]:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug(
            "get_recent_kfactor_sessions called limit=%d accepted_only=%s", limit, accepted_only
        )

        try:
            stmt = select(car_heater_kfactor_session)
            if accepted_only:
                stmt = stmt.where(car_heater_kfactor_session.c.accepted == True)  # noqa: E712
            stmt = stmt.order_by(
                car_heater_kfactor_session.c.start_ts.desc(),
                car_heater_kfactor_session.c.id.desc(),
            ).limit(limit)

            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            return [
                CarHeaterKFactorSession(
                    id=row["id"],
                    start_ts=row["start_ts"],
                    end_ts=row["end_ts"],
                    auto_window_date=row["auto_window_date"],
                    auto_window_start=row["auto_window_start"],
                    auto_window_stop=row["auto_window_stop"],
                    heater_mode=row["heater_mode"],
                    outside_t_mean=row["outside_t_mean"],
                    outside_t_min=row["outside_t_min"],
                    outside_t_max=row["outside_t_max"],
                    wind_mean=row["wind_mean"],
                    cabin_t_start=row["cabin_t_start"],
                    cabin_t_end=row["cabin_t_end"],
                    cabin_t_max=row["cabin_t_max"],
                    duration_s=row["duration_s"],
                    sample_count=row["sample_count"],
                    flags_json=row["flags_json"],
                    quality_score=row["quality_score"],
                    accepted=bool(row["accepted"]),
                )
                for row in rows
            ]
        except Exception as e:
            logger.exception("Error getting recent kfactor sessions: %s", e)
            raise

    def get_recent_kfactor_results(
        self,
        *,
        limit: int = 50,
        promoted_only: bool = False,
    ) -> list[CarHeaterKFactorResult]:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug(
            "get_recent_kfactor_results called limit=%d promoted_only=%s", limit, promoted_only
        )

        try:
            stmt = select(car_heater_kfactor_result)
            if promoted_only:
                stmt = stmt.where(car_heater_kfactor_result.c.promoted == True)  # noqa: E712
            stmt = stmt.order_by(
                car_heater_kfactor_result.c.created_ts.desc(),
                car_heater_kfactor_result.c.id.desc(),
            ).limit(limit)

            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            return [
                CarHeaterKFactorResult(
                    id=row["id"],
                    session_id=row["session_id"],
                    model_version=row["model_version"],
                    k_loss_W_per_K=row["k_loss_W_per_K"],
                    eta=row["eta"],
                    rmse_C=row["rmse_C"],
                    r2=row["r2"],
                    confidence=row["confidence"],
                    promoted=bool(row["promoted"]),
                    created_ts=row["created_ts"],
                )
                for row in rows
            ]
        except Exception as e:
            logger.exception("Error getting recent kfactor results: %s", e)
            raise

    def get_kfactor_result_for_session(
        self,
        *,
        session_id: int,
    ) -> CarHeaterKFactorResult | None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_kfactor_result_for_session called session_id=%s", session_id)

        try:
            stmt = (
                select(car_heater_kfactor_result)
                .where(car_heater_kfactor_result.c.session_id == session_id)
                .order_by(
                    car_heater_kfactor_result.c.created_ts.desc(),
                    car_heater_kfactor_result.c.id.desc(),
                )
                .limit(1)
            )

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug(
                    "get_kfactor_result_for_session no row found session_id=%s", session_id
                )
                return None

            return CarHeaterKFactorResult(
                id=row["id"],
                session_id=row["session_id"],
                model_version=row["model_version"],
                k_loss_W_per_K=row["k_loss_W_per_K"],
                eta=row["eta"],
                rmse_C=row["rmse_C"],
                r2=row["r2"],
                confidence=row["confidence"],
                promoted=bool(row["promoted"]),
                created_ts=row["created_ts"],
            )
        except Exception as e:
            logger.exception("Error getting kfactor result for session: %s", e)
            raise

    def get_all_bucket_params(self) -> list[CarHeaterKFactorBucketParams]:
        """Get all bucket parameters for coverage display."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_all_bucket_params called")

        try:
            stmt = select(car_heater_kfactor_bucket_params).order_by(
                car_heater_kfactor_bucket_params.c.t_bucket.asc(),
                car_heater_kfactor_bucket_params.c.wind_bucket.asc(),
            )

            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            return [
                CarHeaterKFactorBucketParams(
                    id=row["id"],
                    t_bucket=row["t_bucket"],
                    wind_bucket=_decode_wind_bucket(row["wind_bucket"]),
                    k_loss_W_per_K=row["k_loss_W_per_K"],
                    eta=row["eta"],
                    updated_ts=row["updated_ts"],
                    source=row["source"],
                )
                for row in rows
            ]
        except Exception as e:
            logger.exception("Error getting all bucket params: %s", e)
            raise

    def get_calibration_stats(self, lookback_days: int = 7) -> dict:
        """Get calibration statistics for dashboard display."""
        from datetime import datetime, timedelta

        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_calibration_stats called lookback_days=%d", lookback_days)

        try:
            with sa_engine.connect() as conn:
                # Total sessions
                stmt = select(func.count()).select_from(car_heater_kfactor_session)
                total_sessions = conn.execute(stmt).scalar() or 0

                # Accepted sessions
                stmt = (
                    select(func.count())
                    .select_from(car_heater_kfactor_session)
                    .where(
                        car_heater_kfactor_session.c.accepted == True  # noqa: E712
                    )
                )
                accepted_sessions = conn.execute(stmt).scalar() or 0

                # Sessions in last N days
                cutoff = datetime.now() - timedelta(days=lookback_days)
                stmt = (
                    select(func.count())
                    .select_from(car_heater_kfactor_session)
                    .where(car_heater_kfactor_session.c.start_ts >= cutoff)
                )
                sessions_recent = conn.execute(stmt).scalar() or 0

                # Average quality of accepted sessions
                stmt = select(func.avg(car_heater_kfactor_session.c.quality_score)).where(
                    car_heater_kfactor_session.c.accepted == True  # noqa: E712
                )
                avg_quality = conn.execute(stmt).scalar() or 0.0

                # Average k_loss and eta from bucket params
                stmt = select(
                    func.avg(car_heater_kfactor_bucket_params.c.k_loss_W_per_K),
                    func.avg(car_heater_kfactor_bucket_params.c.eta),
                )
                result = conn.execute(stmt).first()
                avg_k_loss = result[0] if result and result[0] else None
                avg_eta = result[1] if result and result[1] else None

                # Buckets covered (unique t_bucket, wind_bucket combinations)
                stmt = select(func.count()).select_from(car_heater_kfactor_bucket_params)
                buckets_covered = conn.execute(stmt).scalar() or 0

                # Days since last accepted session
                stmt = (
                    select(car_heater_kfactor_session.c.start_ts)
                    .where(car_heater_kfactor_session.c.accepted == True)  # noqa: E712
                    .order_by(car_heater_kfactor_session.c.start_ts.desc())
                    .limit(1)
                )
                last_row = conn.execute(stmt).first()

            days_since_last = None
            if last_row and last_row[0]:
                try:
                    last_ts = last_row[0]
                    if isinstance(last_ts, str):
                        last_ts = datetime.fromisoformat(last_ts.replace(" ", "T"))
                    now = datetime.now(last_ts.tzinfo) if last_ts.tzinfo else datetime.now()
                    days_since_last = (now - last_ts).days
                except Exception:
                    pass

            # Total possible buckets: temps from -30 to +10 (9 buckets) x wind 4 buckets = 36
            # But we use 5°C buckets: -30, -25, -20, -15, -10, -5, 0, 5, 10 = 9 temp buckets
            # Wind: 0-2, 2-5, 5-10, 10+ = 4 wind buckets + 1 for unknown
            buckets_total = 9 * 5  # 45 possible combinations

            return {
                "total_sessions": total_sessions,
                "accepted_sessions": accepted_sessions,
                "sessions_last_7d": sessions_recent,
                "avg_quality": round(avg_quality, 2) if avg_quality else 0.0,
                "avg_k_loss": round(avg_k_loss, 1) if avg_k_loss else None,
                "avg_eta": round(avg_eta, 3) if avg_eta else None,
                "buckets_covered": buckets_covered,
                "buckets_total": buckets_total,
                "coverage_pct": round(100 * buckets_covered / buckets_total, 1)
                if buckets_total > 0
                else 0,
                "days_since_last_session": days_since_last,
            }
        except Exception as e:
            logger.exception("Error getting calibration stats: %s", e)
            raise

    def get_sessions_with_results(self, limit: int = 10) -> list[dict]:
        """Get recent sessions joined with their fit results for display."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_sessions_with_results called limit=%d", limit)

        try:
            s = car_heater_kfactor_session
            r = car_heater_kfactor_result

            # Build the LEFT JOIN
            j = outerjoin(
                s,
                r,
                and_(
                    r.c.session_id == s.c.id,
                    r.c.promoted == True,  # noqa: E712
                ),
            )

            stmt = (
                select(
                    s.c.id,
                    s.c.start_ts,
                    s.c.end_ts,
                    s.c.heater_mode,
                    s.c.outside_t_mean,
                    s.c.wind_mean,
                    s.c.cabin_t_start,
                    s.c.cabin_t_end,
                    s.c.duration_s,
                    s.c.sample_count,
                    s.c.quality_score,
                    s.c.accepted,
                    s.c.flags_json,
                    r.c.k_loss_W_per_K,
                    r.c.eta,
                    r.c.rmse_C,
                    r.c.r2,
                )
                .select_from(j)
                .order_by(s.c.start_ts.desc())
                .limit(limit)
            )

            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            return [
                {
                    "id": row["id"],
                    "start_ts": row["start_ts"],
                    "end_ts": row["end_ts"],
                    "mode": row["heater_mode"] or "passive",
                    "outside_t_mean": row["outside_t_mean"],
                    "wind_mean": row["wind_mean"],
                    "cabin_t_start": row["cabin_t_start"],
                    "cabin_t_end": row["cabin_t_end"],
                    "duration_s": row["duration_s"],
                    "sample_count": row["sample_count"],
                    "quality_score": row["quality_score"],
                    "accepted": bool(row["accepted"]),
                    "flags_json": row["flags_json"],
                    "fit": {
                        "k_loss": row["k_loss_W_per_K"],
                        "eta": row["eta"],
                        "rmse_C": row["rmse_C"],
                        "r2": row["r2"],
                    }
                    if row["k_loss_W_per_K"] is not None
                    else None,
                }
                for row in rows
            ]
        except Exception as e:
            logger.exception("Error getting sessions with results: %s", e)
            raise

    # --- Migration helper ---
    def migrate_kfactor_to_pg(self, batch_size: int = 1000) -> dict[str, Any]:
        """
        Migrate kfactor calibration data from SQLite to PostgreSQL using bulk inserts.
        Returns dict with migration statistics.

        Tables migrated:
        - car_heater_kfactor_session
        - car_heater_kfactor_result
        - car_heater_kfactor_prediction_outcome
        - car_heater_kfactor_active_params
        - car_heater_kfactor_bucket_params
        - car_heater_kfactor_config
        - car_heater_kfactor_cooldown

        Args:
            batch_size: Number of rows to insert per batch (default 1000)
        """
        import time

        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        stats = {
            "car_heater_kfactor_session": {"migrated": 0, "errors": 0},
            "car_heater_kfactor_result": {"migrated": 0, "errors": 0},
            "car_heater_kfactor_prediction_outcome": {"migrated": 0, "errors": 0},
            "car_heater_kfactor_active_params": {"migrated": 0, "errors": 0},
            "car_heater_kfactor_bucket_params": {"migrated": 0, "errors": 0},
            "car_heater_kfactor_config": {"migrated": 0, "errors": 0},
            "car_heater_kfactor_cooldown": {"migrated": 0, "errors": 0},
        }

        logger.info("=" * 60)
        logger.info("Starting kfactor data migration from SQLite to PostgreSQL")
        logger.info("Batch size: %d rows per transaction", batch_size)
        logger.info("=" * 60)

        # --- 1. Migrate car_heater_kfactor_session ---
        try:
            rows = self.db.fetchall(
                """
                SELECT id, start_ts, end_ts, auto_window_date, auto_window_start,
                       auto_window_stop, heater_mode, outside_t_mean, outside_t_min,
                       outside_t_max, wind_mean, cabin_t_start, cabin_t_end,
                       cabin_t_max, duration_s, sample_count, flags_json,
                       quality_score, accepted
                FROM car_heater_kfactor_session ORDER BY id
                """
            )
            total = len(rows)
            logger.info("📊 kfactor_session: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ kfactor_session: No records to migrate")
            else:
                start_time = time.time()
                batch = []
                for i, row in enumerate(rows, 1):
                    batch.append(
                        {
                            "id": row["id"],
                            "start_ts": row["start_ts"],
                            "end_ts": row["end_ts"],
                            "auto_window_date": row["auto_window_date"],
                            "auto_window_start": row["auto_window_start"],
                            "auto_window_stop": row["auto_window_stop"],
                            "heater_mode": row["heater_mode"],
                            "outside_t_mean": row["outside_t_mean"],
                            "outside_t_min": row["outside_t_min"],
                            "outside_t_max": row["outside_t_max"],
                            "wind_mean": row["wind_mean"],
                            "cabin_t_start": row["cabin_t_start"],
                            "cabin_t_end": row["cabin_t_end"],
                            "cabin_t_max": row["cabin_t_max"],
                            "duration_s": row["duration_s"],
                            "sample_count": row["sample_count"],
                            "flags_json": row["flags_json"],
                            "quality_score": row["quality_score"],
                            "accepted": bool(row["accepted"]),
                        }
                    )

                    if len(batch) >= batch_size or i == total:
                        try:
                            stmt = pg_insert(car_heater_kfactor_session).values(batch)
                            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                            with sa_engine.begin() as conn:
                                conn.execute(stmt)
                            stats["car_heater_kfactor_session"]["migrated"] += len(batch)

                            elapsed = time.time() - start_time
                            progress_pct = (
                                stats["car_heater_kfactor_session"]["migrated"] / total
                            ) * 100
                            rate = (
                                stats["car_heater_kfactor_session"]["migrated"] / elapsed
                                if elapsed > 0
                                else 0
                            )
                            logger.info(
                                "📈 kfactor_session: %d/%d (%.1f%%) | %.0f rows/sec",
                                stats["car_heater_kfactor_session"]["migrated"],
                                total,
                                progress_pct,
                                rate,
                            )
                            batch = []
                        except Exception as e:
                            logger.error("❌ Error migrating kfactor_session batch: %s", e)
                            stats["car_heater_kfactor_session"]["errors"] += len(batch)
                            batch = []
        except Exception as e:
            logger.exception("Error migrating kfactor_session: %s", e)
            stats["car_heater_kfactor_session"]["errors"] += 1

        # --- 2. Migrate car_heater_kfactor_result ---
        try:
            rows = self.db.fetchall(
                """
                SELECT id, session_id, model_version, k_loss_W_per_K, eta,
                       rmse_C, r2, confidence, promoted, created_ts
                FROM car_heater_kfactor_result ORDER BY id
                """
            )
            total = len(rows)
            logger.info("📊 kfactor_result: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ kfactor_result: No records to migrate")
            else:
                start_time = time.time()
                batch = []
                for i, row in enumerate(rows, 1):
                    batch.append(
                        {
                            "id": row["id"],
                            "session_id": row["session_id"],
                            "model_version": row["model_version"],
                            "k_loss_W_per_K": row["k_loss_W_per_K"],
                            "eta": row["eta"],
                            "rmse_C": row["rmse_C"],
                            "r2": row["r2"],
                            "confidence": row["confidence"],
                            "promoted": bool(row["promoted"]),
                            "created_ts": row["created_ts"],
                        }
                    )

                    if len(batch) >= batch_size or i == total:
                        try:
                            stmt = pg_insert(car_heater_kfactor_result).values(batch)
                            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                            with sa_engine.begin() as conn:
                                conn.execute(stmt)
                            stats["car_heater_kfactor_result"]["migrated"] += len(batch)

                            elapsed = time.time() - start_time
                            progress_pct = (
                                stats["car_heater_kfactor_result"]["migrated"] / total
                            ) * 100
                            rate = (
                                stats["car_heater_kfactor_result"]["migrated"] / elapsed
                                if elapsed > 0
                                else 0
                            )
                            logger.info(
                                "📈 kfactor_result: %d/%d (%.1f%%) | %.0f rows/sec",
                                stats["car_heater_kfactor_result"]["migrated"],
                                total,
                                progress_pct,
                                rate,
                            )
                            batch = []
                        except Exception as e:
                            logger.error("❌ Error migrating kfactor_result batch: %s", e)
                            stats["car_heater_kfactor_result"]["errors"] += len(batch)
                            batch = []
        except Exception as e:
            logger.exception("Error migrating kfactor_result: %s", e)
            stats["car_heater_kfactor_result"]["errors"] += 1

        # --- 3. Migrate car_heater_kfactor_prediction_outcome ---
        try:
            rows = self.db.fetchall(
                """
                SELECT id, predicted_minutes, actual_minutes, error_minutes,
                       cabin_start_c, cabin_end_c, target_c, outside_c, created_ts
                FROM car_heater_kfactor_prediction_outcome ORDER BY id
                """
            )
            total = len(rows)
            logger.info("📊 kfactor_prediction_outcome: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ kfactor_prediction_outcome: No records to migrate")
            else:
                start_time = time.time()
                batch = []
                for i, row in enumerate(rows, 1):
                    batch.append(
                        {
                            "id": row["id"],
                            "predicted_minutes": row["predicted_minutes"],
                            "actual_minutes": row["actual_minutes"],
                            "error_minutes": row["error_minutes"],
                            "cabin_start_c": row["cabin_start_c"],
                            "cabin_end_c": row["cabin_end_c"],
                            "target_c": row["target_c"],
                            "outside_c": row["outside_c"],
                            "created_ts": row["created_ts"],
                        }
                    )

                    if len(batch) >= batch_size or i == total:
                        try:
                            stmt = pg_insert(car_heater_kfactor_prediction_outcome).values(batch)
                            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                            with sa_engine.begin() as conn:
                                conn.execute(stmt)
                            stats["car_heater_kfactor_prediction_outcome"]["migrated"] += len(batch)

                            elapsed = time.time() - start_time
                            progress_pct = (
                                stats["car_heater_kfactor_prediction_outcome"]["migrated"] / total
                            ) * 100
                            rate = (
                                stats["car_heater_kfactor_prediction_outcome"]["migrated"] / elapsed
                                if elapsed > 0
                                else 0
                            )
                            logger.info(
                                "📈 kfactor_prediction_outcome: %d/%d (%.1f%%) | %.0f rows/sec",
                                stats["car_heater_kfactor_prediction_outcome"]["migrated"],
                                total,
                                progress_pct,
                                rate,
                            )
                            batch = []
                        except Exception as e:
                            logger.error(
                                "❌ Error migrating kfactor_prediction_outcome batch: %s", e
                            )
                            stats["car_heater_kfactor_prediction_outcome"]["errors"] += len(batch)
                            batch = []
        except Exception as e:
            logger.exception("Error migrating kfactor_prediction_outcome: %s", e)
            stats["car_heater_kfactor_prediction_outcome"]["errors"] += 1

        # --- 4. Migrate car_heater_kfactor_active_params (singleton) ---
        try:
            row = self.db.fetchone(
                """
                SELECT id, k_loss_W_per_K, eta, updated_ts, source
                FROM car_heater_kfactor_active_params WHERE id = 1
                """
            )
            if row:
                logger.info("📊 kfactor_active_params: Found singleton record to migrate")
                try:
                    stmt = pg_insert(car_heater_kfactor_active_params).values(
                        id=1,
                        k_loss_W_per_K=row["k_loss_W_per_K"],
                        eta=row["eta"],
                        updated_ts=row["updated_ts"],
                        source=row["source"],
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "k_loss_W_per_K": stmt.excluded.k_loss_W_per_K,
                            "eta": stmt.excluded.eta,
                            "updated_ts": stmt.excluded.updated_ts,
                            "source": stmt.excluded.source,
                        },
                    )
                    with sa_engine.begin() as conn:
                        conn.execute(stmt)
                    stats["car_heater_kfactor_active_params"]["migrated"] = 1
                    logger.info("✓ kfactor_active_params: Migrated 1 record")
                except Exception as e:
                    logger.error("❌ Error migrating kfactor_active_params: %s", e)
                    stats["car_heater_kfactor_active_params"]["errors"] += 1
            else:
                logger.info("✓ kfactor_active_params: No record to migrate")
        except Exception as e:
            logger.exception("Error migrating kfactor_active_params: %s", e)
            stats["car_heater_kfactor_active_params"]["errors"] += 1

        # --- 5. Migrate car_heater_kfactor_bucket_params ---
        try:
            rows = self.db.fetchall(
                """
                SELECT id, t_bucket, wind_bucket, k_loss_W_per_K, eta, updated_ts, source
                FROM car_heater_kfactor_bucket_params ORDER BY id
                """
            )
            total = len(rows)
            logger.info("📊 kfactor_bucket_params: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ kfactor_bucket_params: No records to migrate")
            else:
                start_time = time.time()
                batch = []
                for i, row in enumerate(rows, 1):
                    batch.append(
                        {
                            "id": row["id"],
                            "t_bucket": row["t_bucket"],
                            "wind_bucket": row["wind_bucket"],
                            "k_loss_W_per_K": row["k_loss_W_per_K"],
                            "eta": row["eta"],
                            "updated_ts": row["updated_ts"],
                            "source": row["source"],
                        }
                    )

                    if len(batch) >= batch_size or i == total:
                        try:
                            stmt = pg_insert(car_heater_kfactor_bucket_params).values(batch)
                            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                            with sa_engine.begin() as conn:
                                conn.execute(stmt)
                            stats["car_heater_kfactor_bucket_params"]["migrated"] += len(batch)

                            elapsed = time.time() - start_time
                            progress_pct = (
                                stats["car_heater_kfactor_bucket_params"]["migrated"] / total
                            ) * 100
                            rate = (
                                stats["car_heater_kfactor_bucket_params"]["migrated"] / elapsed
                                if elapsed > 0
                                else 0
                            )
                            logger.info(
                                "📈 kfactor_bucket_params: %d/%d (%.1f%%) | %.0f rows/sec",
                                stats["car_heater_kfactor_bucket_params"]["migrated"],
                                total,
                                progress_pct,
                                rate,
                            )
                            batch = []
                        except Exception as e:
                            logger.error("❌ Error migrating kfactor_bucket_params batch: %s", e)
                            stats["car_heater_kfactor_bucket_params"]["errors"] += len(batch)
                            batch = []
        except Exception as e:
            logger.exception("Error migrating kfactor_bucket_params: %s", e)
            stats["car_heater_kfactor_bucket_params"]["errors"] += 1

        # --- 6. Migrate car_heater_kfactor_config (singleton) ---
        try:
            row = self.db.fetchone(
                """
                SELECT id, config_json, updated_ts
                FROM car_heater_kfactor_config WHERE id = 1
                """
            )
            if row:
                logger.info("📊 kfactor_config: Found singleton record to migrate")
                try:
                    stmt = pg_insert(car_heater_kfactor_config).values(
                        id=1,
                        config_json=row["config_json"],
                        updated_ts=row["updated_ts"],
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "config_json": stmt.excluded.config_json,
                            "updated_ts": stmt.excluded.updated_ts,
                        },
                    )
                    with sa_engine.begin() as conn:
                        conn.execute(stmt)
                    stats["car_heater_kfactor_config"]["migrated"] = 1
                    logger.info("✓ kfactor_config: Migrated 1 record")
                except Exception as e:
                    logger.error("❌ Error migrating kfactor_config: %s", e)
                    stats["car_heater_kfactor_config"]["errors"] += 1
            else:
                logger.info("✓ kfactor_config: No record to migrate")
        except Exception as e:
            logger.exception("Error migrating kfactor_config: %s", e)
            stats["car_heater_kfactor_config"]["errors"] += 1

        # --- 7. Migrate car_heater_kfactor_cooldown (singleton) ---
        try:
            row = self.db.fetchone(
                """
                SELECT id, cooldown_until, updated_ts
                FROM car_heater_kfactor_cooldown WHERE id = 1
                """
            )
            if row:
                logger.info("📊 kfactor_cooldown: Found singleton record to migrate")
                try:
                    stmt = pg_insert(car_heater_kfactor_cooldown).values(
                        id=1,
                        cooldown_until=row["cooldown_until"],
                        updated_ts=row["updated_ts"],
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "cooldown_until": stmt.excluded.cooldown_until,
                            "updated_ts": stmt.excluded.updated_ts,
                        },
                    )
                    with sa_engine.begin() as conn:
                        conn.execute(stmt)
                    stats["car_heater_kfactor_cooldown"]["migrated"] = 1
                    logger.info("✓ kfactor_cooldown: Migrated 1 record")
                except Exception as e:
                    logger.error("❌ Error migrating kfactor_cooldown: %s", e)
                    stats["car_heater_kfactor_cooldown"]["errors"] += 1
            else:
                logger.info("✓ kfactor_cooldown: No record to migrate")
        except Exception as e:
            logger.exception("Error migrating kfactor_cooldown: %s", e)
            stats["car_heater_kfactor_cooldown"]["errors"] += 1

        logger.info("=" * 60)
        logger.info("KFactor migration complete. Summary:")
        for table, counts in stats.items():
            logger.info("  %s: %d migrated, %d errors", table, counts["migrated"], counts["errors"])
        logger.info("=" * 60)

        return stats
