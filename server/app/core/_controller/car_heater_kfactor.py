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
