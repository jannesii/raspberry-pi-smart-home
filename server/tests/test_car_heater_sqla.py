"""
Unit tests for car_heater.py SQLAlchemy migration.

Tests cover:
- CarHeaterStatus recording and retrieval (time-series data)
- ChargeModeState CRUD (singleton)
- KeepAtTempSettings CRUD (singleton)
- Migration helper
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.core import Controller
from app.core.models import CarHeaterStatus
from app.core.schema import metadata
from app.services.car_heater import ChargeModeState, KeepAtTempSettings


@pytest.fixture
def temp_db():
    """Create a temporary PostgreSQL-like database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield str(db_path)


@pytest.fixture
def controller(temp_db: str):
    """Create a controller with SQLAlchemy engine for testing."""
    ctrl = Controller(db_path=temp_db)

    # Create SQLAlchemy engine pointing to a temp PostgreSQL-like SQLite
    pg_db_path = temp_db.replace(".db", "_pg.db")
    engine = create_engine(f"sqlite:///{pg_db_path}", echo=False)

    # Create tables from schema
    metadata.create_all(engine)

    ctrl._sa_engine = engine
    yield ctrl
    engine.dispose()


def make_status(
    timestamp: datetime | str | None = None,
    is_heater_on: bool = True,
    instant_power_w: float | None = 1000.0,
    source: str | None = "test",
    **kwargs,
) -> CarHeaterStatus:
    """Helper to create CarHeaterStatus with sensible defaults."""
    if timestamp is None:
        timestamp = datetime.now(UTC)
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()

    return CarHeaterStatus(
        id=kwargs.get("id"),
        timestamp=timestamp,
        is_heater_on=is_heater_on,
        instant_power_w=instant_power_w,
        voltage_v=kwargs.get("voltage_v"),
        current_a=kwargs.get("current_a"),
        energy_total_wh=kwargs.get("energy_total_wh"),
        energy_last_min_wh=kwargs.get("energy_last_min_wh"),
        energy_ts=kwargs.get("energy_ts"),
        device_temp_c=kwargs.get("device_temp_c"),
        device_temp_f=kwargs.get("device_temp_f"),
        ambient_temp=kwargs.get("ambient_temp"),
        source=source,
    )


class TestCarHeaterStatus:
    """Tests for car heater status time-series recording and retrieval."""

    def test_record_status_creates_new_entry(self, controller: Controller):
        """Test that recording a status creates a new entry with returned ID."""
        status = make_status(
            timestamp=datetime.now(UTC),
            is_heater_on=True,
            instant_power_w=1500.0,
            voltage_v=230.0,
            current_a=6.5,
            energy_total_wh=12345.0,
            energy_last_min_wh=25.0,
            energy_ts=int(datetime.now(UTC).timestamp()),
            device_temp_c=35.0,
            device_temp_f=95.0,
            ambient_temp=-5.0,
            source="test",
        )

        result = controller.record_car_heater_status(status)

        assert result.id is not None
        assert result.id > 0
        assert result.instant_power_w == 1500.0

    def test_record_multiple_statuses(self, controller: Controller):
        """Test recording multiple statuses creates sequential IDs."""
        ids = []
        for i in range(3):
            status = make_status(
                timestamp=datetime.now(UTC) + timedelta(minutes=i),
                is_heater_on=True,
                instant_power_w=1000.0 + i * 100,
                source="test",
            )
            result = controller.record_car_heater_status(status)
            ids.append(result.id)

        assert ids[0] < ids[1] < ids[2]

    def test_get_last_status_returns_most_recent(self, controller: Controller):
        """Test that get_last returns the most recent entry."""
        for i in range(3):
            status = make_status(
                timestamp=datetime.now(UTC) + timedelta(minutes=i),
                is_heater_on=True,
                instant_power_w=1000.0 + i * 100,
                source=f"test_{i}",
            )
            controller.record_car_heater_status(status)

        result = controller.get_last_car_heater_status()

        assert result is not None
        assert result.source == "test_2"
        assert result.instant_power_w == 1200.0

    def test_get_last_status_empty_db(self, controller: Controller):
        """Test that get_last returns None when no records exist."""
        result = controller.get_last_car_heater_status()
        assert result is None

    def test_get_status_between_timestamps(self, controller: Controller):
        """Test retrieving status records between two timestamps."""
        base_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)

        for i in range(5):
            status = make_status(
                timestamp=base_time + timedelta(hours=i),
                is_heater_on=True,
                instant_power_w=1000.0 + i * 100,
                source=f"test_{i}",
            )
            controller.record_car_heater_status(status)

        # Get middle 3 records (method expects ISO strings)
        start = (base_time + timedelta(hours=1)).isoformat()
        end = (base_time + timedelta(hours=3)).isoformat()
        results = controller.get_car_heater_status_between(start, end)

        assert len(results) == 3
        assert results[0].source == "test_1"
        assert results[2].source == "test_3"

    def test_get_status_between_empty_range(self, controller: Controller):
        """Test that empty range returns empty list."""
        base_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)

        status = make_status(
            timestamp=base_time,
            is_heater_on=True,
            instant_power_w=1000.0,
            source="test",
        )
        controller.record_car_heater_status(status)

        # Query outside range (method expects ISO strings)
        start = (base_time + timedelta(days=1)).isoformat()
        end = (base_time + timedelta(days=2)).isoformat()
        results = controller.get_car_heater_status_between(start, end)

        assert len(results) == 0

    def test_get_recent_status(self, controller: Controller):
        """Test retrieving recent status records with limit."""
        for i in range(10):
            status = make_status(
                timestamp=datetime.now(UTC) + timedelta(minutes=i),
                is_heater_on=True,
                instant_power_w=1000.0 + i * 100,
                source=f"test_{i}",
            )
            controller.record_car_heater_status(status)

        results = controller.get_recent_car_heater_status(5)

        assert len(results) == 5
        # Should be in descending order
        assert results[0].source == "test_9"
        assert results[4].source == "test_5"

    def test_get_recent_status_fewer_than_limit(self, controller: Controller):
        """Test get_recent when fewer records exist than limit."""
        for i in range(3):
            status = make_status(
                timestamp=datetime.now(UTC) + timedelta(minutes=i),
                is_heater_on=True,
                instant_power_w=1000.0,
                source=f"test_{i}",
            )
            controller.record_car_heater_status(status)

        results = controller.get_recent_car_heater_status(10)

        assert len(results) == 3

    def test_get_status_for_date(self, controller: Controller):
        """Test retrieving status records for a specific date."""
        # Create records across multiple days
        day1 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        day2 = datetime(2024, 1, 16, 10, 0, 0, tzinfo=UTC)
        day3 = datetime(2024, 1, 17, 10, 0, 0, tzinfo=UTC)

        for ts in [day1, day2, day2 + timedelta(hours=5), day3]:
            status = make_status(
                timestamp=ts,
                is_heater_on=True,
                instant_power_w=1000.0,
                source=ts.strftime("%Y-%m-%d"),
            )
            controller.record_car_heater_status(status)

        # Get records for day2 only
        results = controller.get_car_heater_status_for_date("2024-01-16")

        assert len(results) == 2
        for r in results:
            assert r.source == "2024-01-16"


class TestChargeModeState:
    """Tests for charge mode state singleton operations."""

    def test_get_charge_mode_state_defaults(self, controller: Controller):
        """Test that get returns defaults when no record exists."""
        state = controller.get_charge_mode_state()

        assert state is not None
        assert state.enabled is False
        assert state.threshold_w == 20.0  # Default value from ChargeModeState dataclass
        assert state.power_cut is False

    def test_save_and_get_charge_mode_state(self, controller: Controller):
        """Test saving and retrieving charge mode state."""
        state = ChargeModeState(
            enabled=True,
            threshold_w=500.0,
            power_cut=True,
            power_cut_at=datetime.now(UTC).isoformat(),
            last_instant_power_w=450.0,
            seen_above_threshold=True,
        )

        controller.save_charge_mode_state(state)
        result = controller.get_charge_mode_state()

        assert result.enabled is True
        assert result.threshold_w == 500.0
        assert result.power_cut is True
        assert result.last_instant_power_w == 450.0
        assert result.seen_above_threshold is True

    def test_update_charge_mode_state(self, controller: Controller):
        """Test that saving updates existing record."""
        # Save initial state
        state1 = ChargeModeState(enabled=True, threshold_w=500.0)
        controller.save_charge_mode_state(state1)

        # Update state
        state2 = ChargeModeState(enabled=False, threshold_w=600.0)
        controller.save_charge_mode_state(state2)

        result = controller.get_charge_mode_state()

        assert result.enabled is False
        assert result.threshold_w == 600.0

    def test_charge_mode_state_with_power_cut_at(self, controller: Controller):
        """Test handling of power_cut_at timestamp."""
        power_cut_time = datetime.now(UTC).isoformat()
        state = ChargeModeState(
            enabled=True,
            threshold_w=100.0,
            power_cut=True,
            power_cut_at=power_cut_time,
            last_instant_power_w=50.0,
            seen_above_threshold=True,
        )

        controller.save_charge_mode_state(state)
        result = controller.get_charge_mode_state()

        assert result.enabled is True
        assert result.power_cut is True
        assert result.power_cut_at == power_cut_time


class TestKeepAtTempSettings:
    """Tests for keep-at-temp settings singleton operations."""

    def test_get_keep_at_temp_defaults(self, controller: Controller):
        """Test that get returns defaults when no record exists."""
        settings = controller.get_keep_at_temp_settings()

        assert settings is not None
        # Default KeepAtTempSettings has None values
        assert settings.target_temperature_c is None
        assert settings.hysteresis_c is None
        assert settings.enabled is None

    def test_save_and_get_keep_at_temp_settings(self, controller: Controller):
        """Test saving and retrieving keep-at-temp settings."""
        settings = KeepAtTempSettings(
            target_temperature_c=20.0,
            hysteresis_c=2.0,
            enabled=True,
        )

        controller.save_keep_at_temp_settings(settings)
        result = controller.get_keep_at_temp_settings()

        assert result.target_temperature_c == 20.0
        assert result.hysteresis_c == 2.0
        assert result.enabled is True

    def test_update_keep_at_temp_settings(self, controller: Controller):
        """Test that saving updates existing record."""
        # Save initial settings
        settings1 = KeepAtTempSettings(
            target_temperature_c=18.0,
            hysteresis_c=1.5,
            enabled=True,
        )
        controller.save_keep_at_temp_settings(settings1)

        # Update settings
        settings2 = KeepAtTempSettings(
            target_temperature_c=22.0,
            hysteresis_c=2.5,
            enabled=False,
        )
        controller.save_keep_at_temp_settings(settings2)

        result = controller.get_keep_at_temp_settings()

        assert result.target_temperature_c == 22.0
        assert result.hysteresis_c == 2.5
        assert result.enabled is False

    def test_keep_at_temp_with_none_values(self, controller: Controller):
        """Test handling of None values in keep-at-temp settings."""
        settings = KeepAtTempSettings(
            target_temperature_c=None,
            hysteresis_c=None,
            enabled=False,
        )

        controller.save_keep_at_temp_settings(settings)
        result = controller.get_keep_at_temp_settings()

        assert result.target_temperature_c is None
        assert result.hysteresis_c is None
        assert result.enabled is False


class TestCarHeaterMigration:
    """Tests for car heater migration helper.

    Note: These tests validate the migration method signature and empty state handling.
    Full migration tests that write to SQLite require a different test setup.
    """

    def test_migrate_empty_database(self, controller: Controller):
        """Test migration with no source data."""
        stats = controller.migrate_car_heater_to_pg()

        assert stats["car_heater_status"]["migrated"] == 0
        assert stats["car_heater_status"]["errors"] == 0
        assert stats["car_heater_charge_mode"]["migrated"] == 0
        assert stats["car_heater_keep_at_temp"]["migrated"] == 0

    def test_migrate_returns_expected_stats_structure(self, controller: Controller):
        """Test that migration returns expected stats structure."""
        stats = controller.migrate_car_heater_to_pg(batch_size=100)

        # Verify structure
        assert "car_heater_status" in stats
        assert "car_heater_charge_mode" in stats
        assert "car_heater_keep_at_temp" in stats

        for table_stats in stats.values():
            assert "migrated" in table_stats
            assert "errors" in table_stats


class TestEdgeCases:
    """Edge case tests for car heater functionality."""

    def test_status_with_minimal_required_data(self, controller: Controller):
        """Test recording status with minimal required data only."""
        status = make_status(
            timestamp=datetime.now(UTC),
            is_heater_on=False,
            instant_power_w=0.0,  # Required - NOT NULL
            voltage_v=None,
            current_a=None,
            energy_total_wh=None,
            energy_last_min_wh=None,
            energy_ts=None,
            device_temp_c=None,
            device_temp_f=None,
            ambient_temp=None,
            source="test",
        )

        result = controller.record_car_heater_status(status)

        assert result.id is not None
        assert result.is_heater_on is False
        assert result.instant_power_w == 0.0

    def test_status_with_extreme_values(self, controller: Controller):
        """Test recording status with extreme but valid values."""
        status = make_status(
            timestamp=datetime.now(UTC),
            is_heater_on=True,
            instant_power_w=10000.0,  # 10kW
            voltage_v=250.0,
            current_a=43.5,
            energy_total_wh=999999.0,
            energy_last_min_wh=166.67,
            energy_ts=int(datetime.now(UTC).timestamp()),
            device_temp_c=85.0,
            device_temp_f=185.0,
            ambient_temp=-40.0,
            source="extreme_test",
        )

        _ = controller.record_car_heater_status(status)
        retrieved = controller.get_last_car_heater_status()

        assert retrieved.instant_power_w == 10000.0
        assert retrieved.ambient_temp == -40.0

    def test_charge_mode_toggle_rapidly(self, controller: Controller):
        """Test rapidly toggling charge mode state."""
        for i in range(10):
            state = ChargeModeState(
                enabled=(i % 2 == 0),
                threshold_w=float(i * 100) if i > 0 else 20.0,
            )
            controller.save_charge_mode_state(state)

        final = controller.get_charge_mode_state()
        assert final.enabled is False  # Last iteration: 9 % 2 == 1
        assert final.threshold_w == 900.0
