import logging

from .. import (
    CarHeaterStatus,
)
from ...services.car_heater.car_heater_models import ChargeModeState, KeepAtTempSettings

logger = logging.getLogger(__name__)

class CarHeaterMixin:
    def record_car_heater_status(self, status: CarHeaterStatus) -> CarHeaterStatus:
        """
        Insert a new car heater status row and return it with id set.
        Uses the timestamp provided in the CarHeaterStatus.
        """
        self.db.execute_query(
            """
            INSERT INTO car_heater_status (
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                status.timestamp,
                1 if status.is_heater_on else 0,
                float(status.instant_power_w),
                status.voltage_v,
                status.current_a,
                status.energy_total_wh,
                status.energy_last_min_wh,
                status.energy_ts,
                status.device_temp_c,
                status.device_temp_f,
                status.ambient_temp,
                status.source,
            ),
        )

        # Fetch the just-inserted row (same pattern as other record_* helpers)
        row = self.db.fetchone(
            """
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            ORDER BY id DESC
            LIMIT 1
            """
        )
        if row is None:
            raise RuntimeError(
                "Failed to retrieve inserted car_heater_status record")

        return CarHeaterStatus(
            id=row["id"],
            timestamp=row["timestamp"],
            is_heater_on=bool(row["is_heater_on"]),
            instant_power_w=row["instant_power_w"],
            voltage_v=row["voltage_v"],
            current_a=row["current_a"],
            energy_total_wh=row["energy_total_wh"],
            energy_last_min_wh=row["energy_last_min_wh"],
            energy_ts=row["energy_ts"],
            device_temp_c=row["device_temp_c"],
            device_temp_f=row["device_temp_f"],
            ambient_temp=row["ambient_temp"],
            source=row["source"],
        )

    def get_last_car_heater_status(self) -> CarHeaterStatus | None:
        """
        Return the most recent car heater status row, or None if table is empty.
        """
        row = self.db.fetchone(
            """
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """
        )
        if row is None:
            return None

        return CarHeaterStatus(
            id=row["id"],
            timestamp=row["timestamp"],
            is_heater_on=bool(row["is_heater_on"]),
            instant_power_w=row["instant_power_w"],
            voltage_v=row["voltage_v"],
            current_a=row["current_a"],
            energy_total_wh=row["energy_total_wh"],
            energy_last_min_wh=row["energy_last_min_wh"],
            energy_ts=row["energy_ts"],
            device_temp_c=row["device_temp_c"],
            device_temp_f=row["device_temp_f"],
            ambient_temp=row["ambient_temp"],
            source=row["source"],
        )

    def get_car_heater_status_between(
        self,
        start_iso: str,
        end_iso: str,
    ) -> list[CarHeaterStatus]:
        """
        Get all car heater status rows between two ISO timestamps (inclusive),
        ordered by timestamp.
        """
        rows = self.db.fetchall(
            """
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp, id
            """,
            (start_iso, end_iso),
        )

        return [
            CarHeaterStatus(
                id=row["id"],
                timestamp=row["timestamp"],
                is_heater_on=bool(row["is_heater_on"]),
                instant_power_w=row["instant_power_w"],
                voltage_v=row["voltage_v"],
                current_a=row["current_a"],
                energy_total_wh=row["energy_total_wh"],
                energy_last_min_wh=row["energy_last_min_wh"],
                energy_ts=row["energy_ts"],
                device_temp_c=row["device_temp_c"],
                device_temp_f=row["device_temp_f"],
                ambient_temp=row["ambient_temp"],
                source=row["source"],
            )
            for row in rows
        ]

    def get_recent_car_heater_status(
        self,
        limit: int = 200,
    ) -> list[CarHeaterStatus]:
        """
        Get the latest N car heater status rows, newest first.
        Handy for graphs.
        """
        rows = self.db.fetchall(
            f"""
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )

        return [
            CarHeaterStatus(
                id=row["id"],
                timestamp=row["timestamp"],
                is_heater_on=bool(row["is_heater_on"]),
                instant_power_w=row["instant_power_w"],
                voltage_v=row["voltage_v"],
                current_a=row["current_a"],
                energy_total_wh=row["energy_total_wh"],
                energy_last_min_wh=row["energy_last_min_wh"],
                energy_ts=row["energy_ts"],
                device_temp_c=row["device_temp_c"],
                device_temp_f=row["device_temp_f"],
                ambient_temp=row["ambient_temp"],
                source=row["source"],
            )
            for row in rows
        ]

    def get_car_heater_status_for_date(
        self,
        date_str: str,
    ) -> list[CarHeaterStatus]:
        """
        Return all car heater status records for a given local date.
        """
        rows = self.db.fetchall(
            """
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            WHERE date(timestamp) = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (date_str,)
        )
        return [
            CarHeaterStatus(
                id=row["id"],
                timestamp=row["timestamp"],
                is_heater_on=bool(row["is_heater_on"]),
                instant_power_w=row["instant_power_w"],
                voltage_v=row["voltage_v"],
                current_a=row["current_a"],
                energy_total_wh=row["energy_total_wh"],
                energy_last_min_wh=row["energy_last_min_wh"],
                energy_ts=row["energy_ts"],
                device_temp_c=row["device_temp_c"],
                device_temp_f=row["device_temp_f"],
                ambient_temp=row["ambient_temp"],
                source=row["source"],
            )
            for row in rows
        ]

    # --- Car heater charge mode state ---
    def get_charge_mode_state(self) -> ChargeModeState:
        """Load ChargeModeState from the database, or return defaults if not found."""
        row = self.db.fetchone(
            """
            SELECT enabled, threshold_w, power_cut, power_cut_at,
                   last_instant_power_w, seen_above_threshold
              FROM car_heater_charge_mode
             WHERE id = 1
            """
        )
        if row is None:
            return ChargeModeState()
        return ChargeModeState(
            enabled=bool(row['enabled']),
            threshold_w=float(row['threshold_w']),
            power_cut=bool(row['power_cut']),
            power_cut_at=row['power_cut_at'],
            last_instant_power_w=float(
                row['last_instant_power_w']) if row['last_instant_power_w'] is not None else None,
            seen_above_threshold=bool(row['seen_above_threshold']),
        )

    def save_charge_mode_state(self, state: ChargeModeState) -> None:
        """Persist ChargeModeState to the database."""
        self.db.execute_query(
            """
            INSERT INTO car_heater_charge_mode (
                id, enabled, threshold_w, power_cut, power_cut_at,
                last_instant_power_w, seen_above_threshold
            )
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled = excluded.enabled,
                threshold_w = excluded.threshold_w,
                power_cut = excluded.power_cut,
                power_cut_at = excluded.power_cut_at,
                last_instant_power_w = excluded.last_instant_power_w,
                seen_above_threshold = excluded.seen_above_threshold
            """,
            (
                1 if state.enabled else 0,
                state.threshold_w,
                1 if state.power_cut else 0,
                state.power_cut_at,
                state.last_instant_power_w,
                1 if state.seen_above_threshold else 0,
            ),
        )

    # --- Car heater keep-at-temp settings ---
    def get_keep_at_temp_settings(self) -> KeepAtTempSettings:
        """Load KeepAtTempSettings from the database, or return defaults if not found."""
        row = self.db.fetchone(
            """
            SELECT target_temperature_c, hysteresis_c, enabled
              FROM car_heater_keep_at_temp
             WHERE id = 1
            """
        )
        if row is None:
            return KeepAtTempSettings()
        return KeepAtTempSettings(
            target_temperature_c=float(
                row['target_temperature_c']) if row['target_temperature_c'] is not None else None,
            hysteresis_c=float(
                row['hysteresis_c']) if row['hysteresis_c'] is not None else None,
            enabled=bool(
                row['enabled']) if row['enabled'] is not None else None,
        )

    def save_keep_at_temp_settings(self, settings: KeepAtTempSettings) -> None:
        """Persist KeepAtTempSettings to the database."""
        self.db.execute_query(
            """
            INSERT INTO car_heater_keep_at_temp (
                id, target_temperature_c, hysteresis_c, enabled
            )
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                target_temperature_c = excluded.target_temperature_c,
                hysteresis_c = excluded.hysteresis_c,
                enabled = excluded.enabled
            """,
            (
                settings.target_temperature_c,
                settings.hysteresis_c,
                1 if settings.enabled else 0,
            ),
        )
