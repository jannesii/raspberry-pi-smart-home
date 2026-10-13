from __future__ import annotations

import logging
from dataclasses import asdict, fields

from sqlalchemy import Engine, insert, select

from ...services.car_heater.sensor_readings import SENSOR_KEYS
from ..models import CarHeaterSensorFrame
from ..schema import car_heater_sensor_frame

logger = logging.getLogger(__name__)

_FRAME_FIELDS = [f.name for f in fields(CarHeaterSensorFrame) if f.name != "id"]


def _row_to_frame(row) -> CarHeaterSensorFrame:
    return CarHeaterSensorFrame(id=row["id"], **{name: row[name] for name in _FRAME_FIELDS})


class CarHeaterSensorMixin:
    def record_car_heater_sensor_frame(self, frame: CarHeaterSensorFrame) -> CarHeaterSensorFrame:
        """Insert one glass/cabin sensor frame and return it with id set."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        values = {name: value for name, value in asdict(frame).items() if name != "id"}
        stmt = (
            insert(car_heater_sensor_frame).values(**values).returning(car_heater_sensor_frame.c.id)
        )
        with sa_engine.begin() as conn:
            new_id = conn.execute(stmt).scalar_one()
        logger.debug(
            "record_car_heater_sensor_frame id=%s received_at=%s states=%s/%s/%s",
            new_id,
            frame.received_at,
            frame.glass_reference_state,
            frame.glass_comparison_state,
            frame.cabin_state,
        )
        return CarHeaterSensorFrame(id=new_id, **values)

    def get_car_heater_sensor_frames_between(
        self, start_iso: str, end_iso: str
    ) -> list[CarHeaterSensorFrame]:
        """Return sensor frames received between two UTC ISO timestamps (inclusive)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        stmt = (
            select(car_heater_sensor_frame)
            .where(car_heater_sensor_frame.c.received_at >= start_iso)
            .where(car_heater_sensor_frame.c.received_at <= end_iso)
            .order_by(car_heater_sensor_frame.c.received_at, car_heater_sensor_frame.c.id)
        )
        with sa_engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [_row_to_frame(row) for row in rows]

    def get_latest_car_heater_sensor_samples(self) -> dict[str, CarHeaterSensorFrame]:
        """Return, per sensor key, the newest frame holding an accepted sample."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        latest: dict[str, CarHeaterSensorFrame] = {}
        with sa_engine.connect() as conn:
            for key in SENSOR_KEYS:
                sampled_col = car_heater_sensor_frame.c[f"{key}_sampled_at"]
                stmt = (
                    select(car_heater_sensor_frame)
                    .where(car_heater_sensor_frame.c[f"{key}_temp_c"].is_not(None))
                    .order_by(sampled_col.desc(), car_heater_sensor_frame.c.id.desc())
                    .limit(1)
                )
                row = conn.execute(stmt).mappings().first()
                if row is not None:
                    latest[key] = _row_to_frame(row)
        return latest
