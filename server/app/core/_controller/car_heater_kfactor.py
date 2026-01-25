import logging

from .. import (
    CarHeaterKFactorActiveParams,
    CarHeaterKFactorBucketParams,
    CarHeaterKFactorConfig,
    CarHeaterKFactorResult,
    CarHeaterKFactorSession,
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
        self.db.execute_query(
            """
            INSERT INTO car_heater_kfactor_session (
                start_ts,
                end_ts,
                auto_window_date,
                auto_window_start,
                auto_window_stop,
                heater_mode,
                outside_t_mean,
                outside_t_min,
                outside_t_max,
                wind_mean,
                cabin_t_start,
                cabin_t_end,
                cabin_t_max,
                duration_s,
                sample_count,
                flags_json,
                quality_score,
                accepted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.start_ts,
                session.end_ts,
                session.auto_window_date,
                session.auto_window_start,
                session.auto_window_stop,
                session.heater_mode,
                session.outside_t_mean,
                session.outside_t_min,
                session.outside_t_max,
                session.wind_mean,
                session.cabin_t_start,
                session.cabin_t_end,
                session.cabin_t_max,
                session.duration_s,
                session.sample_count,
                session.flags_json,
                session.quality_score,
                1 if session.accepted else 0,
            ),
        )

        row = self.db.fetchone(
            """
            SELECT
                id,
                start_ts,
                end_ts,
                auto_window_date,
                auto_window_start,
                auto_window_stop,
                heater_mode,
                outside_t_mean,
                outside_t_min,
                outside_t_max,
                wind_mean,
                cabin_t_start,
                cabin_t_end,
                cabin_t_max,
                duration_s,
                sample_count,
                flags_json,
                quality_score,
                accepted
            FROM car_heater_kfactor_session
            ORDER BY id DESC
            LIMIT 1
            """
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted kfactor session")

        return CarHeaterKFactorSession(
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

    def record_kfactor_result(
        self,
        result: CarHeaterKFactorResult,
    ) -> CarHeaterKFactorResult:
        """Insert a new kfactor result row and return it with id set."""
        self.db.execute_query(
            """
            INSERT INTO car_heater_kfactor_result (
                session_id,
                model_version,
                k_loss_W_per_K,
                eta,
                rmse_C,
                r2,
                confidence,
                promoted,
                created_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.session_id,
                result.model_version,
                result.k_loss_W_per_K,
                result.eta,
                result.rmse_C,
                result.r2,
                result.confidence,
                1 if result.promoted else 0,
                result.created_ts,
            ),
        )

        row = self.db.fetchone(
            """
            SELECT
                id,
                session_id,
                model_version,
                k_loss_W_per_K,
                eta,
                rmse_C,
                r2,
                confidence,
                promoted,
                created_ts
            FROM car_heater_kfactor_result
            ORDER BY id DESC
            LIMIT 1
            """
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted kfactor result")

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

    # --- Active params (single-row settings) ---
    def get_kfactor_active_params(self) -> CarHeaterKFactorActiveParams | None:
        """Load active kfactor params from the database, or return None if not set."""
        row = self.db.fetchone(
            """
            SELECT k_loss_W_per_K, eta, updated_ts, source
              FROM car_heater_kfactor_active_params
             WHERE id = 1
            """
        )
        if row is None:
            return None
        return CarHeaterKFactorActiveParams(
            id=1,
            k_loss_W_per_K=row["k_loss_W_per_K"],
            eta=row["eta"],
            updated_ts=row["updated_ts"],
            source=row["source"],
        )

    def save_kfactor_active_params(self, params: CarHeaterKFactorActiveParams) -> None:
        """Persist active kfactor params to the database."""
        self.db.execute_query(
            """
            INSERT INTO car_heater_kfactor_active_params (
                id, k_loss_W_per_K, eta, updated_ts, source
            )
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                k_loss_W_per_K = excluded.k_loss_W_per_K,
                eta = excluded.eta,
                updated_ts = excluded.updated_ts,
                source = excluded.source
            """,
            (
                params.k_loss_W_per_K,
                params.eta,
                params.updated_ts,
                params.source,
            ),
        )

    # --- Bucketed params (per outside temp + wind bucket) ---
    def get_kfactor_bucket_params(
        self,
        *,
        t_bucket: int,
        wind_bucket: int | None,
    ) -> CarHeaterKFactorBucketParams | None:
        wind_bucket_db = _encode_wind_bucket(wind_bucket)
        row = self.db.fetchone(
            """
            SELECT
                id,
                t_bucket,
                wind_bucket,
                k_loss_W_per_K,
                eta,
                updated_ts,
                source
            FROM car_heater_kfactor_bucket_params
            WHERE t_bucket = ?
              AND wind_bucket = ?
            ORDER BY
                CASE WHEN wind_bucket = ? THEN 0 ELSE 1 END,
                updated_ts DESC,
                id DESC
            LIMIT 1
            """,
            (
                t_bucket,
                wind_bucket_db,
                wind_bucket_db,
            ),
        )
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

    def get_kfactor_bucket_params_any_wind(
        self,
        *,
        t_bucket: int,
    ) -> CarHeaterKFactorBucketParams | None:
        row = self.db.fetchone(
            """
            SELECT
                id,
                t_bucket,
                wind_bucket,
                k_loss_W_per_K,
                eta,
                updated_ts,
                source
            FROM car_heater_kfactor_bucket_params
            WHERE t_bucket = ?
            ORDER BY updated_ts DESC, id DESC
            LIMIT 1
            """,
            (t_bucket,),
        )
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

    def save_kfactor_bucket_params(
        self,
        params: CarHeaterKFactorBucketParams,
    ) -> None:
        wind_bucket_db = _encode_wind_bucket(params.wind_bucket)
        self.db.execute_query(
            """
            INSERT INTO car_heater_kfactor_bucket_params (
                t_bucket, wind_bucket, k_loss_W_per_K, eta, updated_ts, source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(t_bucket, wind_bucket) DO UPDATE SET
                k_loss_W_per_K = excluded.k_loss_W_per_K,
                eta = excluded.eta,
                updated_ts = excluded.updated_ts,
                source = excluded.source
            """,
            (
                params.t_bucket,
                wind_bucket_db,
                params.k_loss_W_per_K,
                params.eta,
                params.updated_ts,
                params.source,
            ),
        )

    # --- KFactor config (single-row settings) ---
    def get_kfactor_config(self) -> CarHeaterKFactorConfig | None:
        row = self.db.fetchone(
            """
            SELECT config_json, updated_ts
              FROM car_heater_kfactor_config
             WHERE id = 1
            """
        )
        if row is None:
            return None
        return CarHeaterKFactorConfig(
            id=1,
            config_json=row["config_json"],
            updated_ts=row["updated_ts"],
        )

    def save_kfactor_config(self, config: CarHeaterKFactorConfig) -> None:
        self.db.execute_query(
            """
            INSERT INTO car_heater_kfactor_config (id, config_json, updated_ts)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                config_json = excluded.config_json,
                updated_ts = excluded.updated_ts
            """,
            (
                config.config_json,
                config.updated_ts,
            ),
        )

    # --- Debug / reporting helpers ---
    def get_recent_kfactor_sessions(
        self,
        *,
        limit: int = 50,
        accepted_only: bool = False,
    ) -> list[CarHeaterKFactorSession]:
        where = "WHERE accepted = 1" if accepted_only else ""
        rows = self.db.fetchall(
            f"""
            SELECT
                id,
                start_ts,
                end_ts,
                auto_window_date,
                auto_window_start,
                auto_window_stop,
                heater_mode,
                outside_t_mean,
                outside_t_min,
                outside_t_max,
                wind_mean,
                cabin_t_start,
                cabin_t_end,
                cabin_t_max,
                duration_s,
                sample_count,
                flags_json,
                quality_score,
                accepted
            FROM car_heater_kfactor_session
            {where}
            ORDER BY start_ts DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
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

    def get_recent_kfactor_results(
        self,
        *,
        limit: int = 50,
        promoted_only: bool = False,
    ) -> list[CarHeaterKFactorResult]:
        where = "WHERE promoted = 1" if promoted_only else ""
        rows = self.db.fetchall(
            f"""
            SELECT
                id,
                session_id,
                model_version,
                k_loss_W_per_K,
                eta,
                rmse_C,
                r2,
                confidence,
                promoted,
                created_ts
            FROM car_heater_kfactor_result
            {where}
            ORDER BY created_ts DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
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

    def get_all_bucket_params(self) -> list[CarHeaterKFactorBucketParams]:
        """Get all bucket parameters for coverage display."""
        rows = self.db.fetchall(
            """
            SELECT
                id,
                t_bucket,
                wind_bucket,
                k_loss_W_per_K,
                eta,
                updated_ts,
                source
            FROM car_heater_kfactor_bucket_params
            ORDER BY t_bucket ASC, wind_bucket ASC
            """
        )
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

    def get_calibration_stats(self, lookback_days: int = 7) -> dict:
        """Get calibration statistics for dashboard display."""
        # Total sessions
        total_row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM car_heater_kfactor_session"
        )
        total_sessions = total_row["cnt"] if total_row else 0

        # Accepted sessions
        accepted_row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM car_heater_kfactor_session WHERE accepted = 1"
        )
        accepted_sessions = accepted_row["cnt"] if accepted_row else 0

        # Sessions in last N days
        recent_row = self.db.fetchone(
            """
            SELECT COUNT(*) as cnt FROM car_heater_kfactor_session
            WHERE start_ts >= datetime('now', ?)
            """,
            (f"-{lookback_days} days",),
        )
        sessions_recent = recent_row["cnt"] if recent_row else 0

        # Average quality of accepted sessions
        avg_row = self.db.fetchone(
            """
            SELECT AVG(quality_score) as avg_quality
            FROM car_heater_kfactor_session
            WHERE accepted = 1
            """
        )
        avg_quality = avg_row["avg_quality"] if avg_row and avg_row["avg_quality"] else 0.0

        # Average k_loss and eta from bucket params
        avg_params_row = self.db.fetchone(
            """
            SELECT AVG(k_loss_W_per_K) as avg_k, AVG(eta) as avg_eta
            FROM car_heater_kfactor_bucket_params
            """
        )
        avg_k_loss = avg_params_row["avg_k"] if avg_params_row and avg_params_row["avg_k"] else None
        avg_eta = avg_params_row["avg_eta"] if avg_params_row and avg_params_row["avg_eta"] else None

        # Buckets covered (unique t_bucket, wind_bucket combinations)
        buckets_row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM car_heater_kfactor_bucket_params"
        )
        buckets_covered = buckets_row["cnt"] if buckets_row else 0

        # Days since last accepted session
        last_row = self.db.fetchone(
            """
            SELECT start_ts FROM car_heater_kfactor_session
            WHERE accepted = 1
            ORDER BY start_ts DESC
            LIMIT 1
            """
        )
        days_since_last = None
        if last_row and last_row["start_ts"]:
            from datetime import datetime
            try:
                last_ts = datetime.fromisoformat(
                    last_row["start_ts"].replace(" ", "T"))
                now = datetime.now(
                    last_ts.tzinfo) if last_ts.tzinfo else datetime.now()
                days_since_last = (now - last_ts).days
            except Exception:
                pass

        # Total possible buckets: temps from -30 to +10 (9 buckets) × wind 4 buckets = 36
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
            "coverage_pct": round(100 * buckets_covered / buckets_total, 1) if buckets_total > 0 else 0,
            "days_since_last_session": days_since_last,
        }

    def get_sessions_with_results(self, limit: int = 10) -> list[dict]:
        """Get recent sessions joined with their fit results for display."""
        rows = self.db.fetchall(
            """
            SELECT
                s.id,
                s.start_ts,
                s.end_ts,
                s.heater_mode,
                s.outside_t_mean,
                s.wind_mean,
                s.cabin_t_start,
                s.cabin_t_end,
                s.duration_s,
                s.sample_count,
                s.quality_score,
                s.accepted,
                s.flags_json,
                r.k_loss_W_per_K,
                r.eta,
                r.rmse_C,
                r.r2
            FROM car_heater_kfactor_session s
            LEFT JOIN car_heater_kfactor_result r ON r.session_id = s.id AND r.promoted = 1
            ORDER BY s.start_ts DESC
            LIMIT ?
            """,
            (limit,),
        )
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
                } if row["k_loss_W_per_K"] is not None else None,
            }
            for row in rows
        ]
