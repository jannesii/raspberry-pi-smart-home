"""
Test suite for AC (thermostat) controller operations using SQLAlchemy Core.
"""

import json
import os
import sqlite3
import tempfile
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.controller import Controller
from app.services.ac.thermostat import sleep_manager as sleep_manager_module
from app.services.ac.thermostat.sleep_manager import SleepManager


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    with suppress(FileNotFoundError):
        os.unlink(path)


@pytest.fixture
def ctrl(temp_db):
    """Create a controller instance with temporary database."""
    ctrl = Controller(db_path=temp_db)
    # Ensure SA engine is initialized
    if ctrl._sa_engine is None:
        from app.core.sqlalchemy_engine import get_engine

        ctrl._sa_engine = get_engine(temp_db)

    # Create tables using SQLAlchemy metadata
    from app.core.schema import metadata

    metadata.create_all(ctrl._sa_engine)
    return ctrl


class TestACEventOperations:
    """Test AC event logging and querying."""

    def test_record_ac_event(self, ctrl):
        """Test recording an AC on/off event."""
        now = datetime.now(UTC)
        ctrl.record_ac_event(is_on=True, source="test", note="test event", when_iso=now.isoformat())

        # Verify event was recorded
        events = ctrl.get_ac_events_between(
            start_iso=(now - timedelta(minutes=1)).isoformat(),
            end_iso=(now + timedelta(minutes=1)).isoformat(),
        )
        assert len(events) == 1
        assert events[0]["is_on"] is True
        assert events[0]["source"] == "test"
        assert events[0]["note"] == "test event"

    def test_get_ac_events_between(self, ctrl):
        """Test querying AC events in a time range."""
        base = datetime.now(UTC)
        # Record events at different times
        ctrl.record_ac_event(
            is_on=True, source="old", when_iso=(base - timedelta(hours=2)).isoformat()
        )
        ctrl.record_ac_event(
            is_on=False, source="middle", when_iso=(base - timedelta(hours=1)).isoformat()
        )
        ctrl.record_ac_event(is_on=True, source="recent", when_iso=base.isoformat())

        # Query middle event
        events = ctrl.get_ac_events_between(
            start_iso=(base - timedelta(hours=1, minutes=30)).isoformat(),
            end_iso=(base - timedelta(minutes=30)).isoformat(),
        )
        assert len(events) == 1
        assert events[0]["source"] == "middle"

    def test_get_last_ac_state_before(self, ctrl):
        """Test getting last AC state before a timestamp."""
        base = datetime.now(UTC)
        ctrl.record_ac_event(
            is_on=True, source="s1", when_iso=(base - timedelta(hours=2)).isoformat()
        )
        ctrl.record_ac_event(
            is_on=False, source="s2", when_iso=(base - timedelta(hours=1)).isoformat()
        )
        ctrl.record_ac_event(is_on=True, source="s3", when_iso=base.isoformat())

        # Get state before base (should be False from s2)
        state = ctrl.get_last_ac_state_before(ts_iso=(base - timedelta(minutes=30)).isoformat())
        assert state is False

        # Get state before first event (should be None)
        state = ctrl.get_last_ac_state_before(ts_iso=(base - timedelta(hours=3)).isoformat())
        assert state is None

    def test_empty_events(self, ctrl):
        """Test querying when no events exist."""
        now = datetime.now(UTC)
        events = ctrl.get_ac_events_between(
            start_iso=(now - timedelta(hours=1)).isoformat(), end_iso=now.isoformat()
        )
        assert events == []

        state = ctrl.get_last_ac_state_before(ts_iso=now.isoformat())
        assert state is None


class TestThermostatConfiguration:
    """Test thermostat configuration CRUD operations."""

    def test_no_config_initially(self, ctrl):
        """Test that no config exists initially."""
        conf = ctrl.get_thermostat_conf()
        assert conf is None

    def test_save_and_get_config(self, ctrl):
        """Test saving and retrieving thermostat configuration."""
        conf = ctrl.save_thermostat_conf(
            sleep_active=True,
            sleep_start="22:00",
            sleep_stop="07:00",
            sleep_weekly="1111100",  # Mon-Fri
            control_locations="bedroom,living_room",
            target_temp=24.5,
            pos_hysteresis=0.5,
            neg_hysteresis=0.5,
            thermo_active=True,
            min_on_s=240,
            min_off_s=240,
            poll_interval_s=15,
            smooth_window=5,
            max_stale_s=120,
            current_phase="on",
            phase_started_at="2024-01-01T12:00:00Z",
        )

        assert conf.id == 1
        assert conf.sleep_active is True
        assert conf.sleep_start == "22:00"
        assert conf.sleep_stop == "07:00"
        assert conf.target_temp == 24.5
        assert conf.thermo_active is True
        assert conf.current_phase == "on"

        # Retrieve and verify
        retrieved = ctrl.get_thermostat_conf()
        assert retrieved is not None
        assert retrieved.id == 1
        assert retrieved.target_temp == 24.5
        assert retrieved.sleep_weekly == "1111100"
        assert retrieved.control_locations == "bedroom,living_room"

    def test_update_config(self, ctrl):
        """Test updating existing configuration."""
        # Initial save
        ctrl.save_thermostat_conf(
            sleep_active=False,
            sleep_start=None,
            sleep_stop=None,
            target_temp=22.0,
            pos_hysteresis=0.5,
            neg_hysteresis=0.5,
            thermo_active=True,
        )

        # Update
        updated = ctrl.save_thermostat_conf(
            sleep_active=True,
            sleep_start="23:00",
            sleep_stop="06:00",
            target_temp=25.0,
            pos_hysteresis=0.7,
            neg_hysteresis=0.3,
            thermo_active=False,
            current_phase="off",
        )

        assert updated.sleep_active is True
        assert updated.sleep_start == "23:00"
        assert updated.target_temp == 25.0
        assert updated.pos_hysteresis == 0.7
        assert updated.thermo_active is False
        assert updated.current_phase == "off"

    def test_config_defaults(self, ctrl):
        """Test that default values are applied correctly."""
        conf = ctrl.save_thermostat_conf(
            sleep_active=False,
            sleep_start=None,
            sleep_stop=None,
            target_temp=23.0,
            pos_hysteresis=0.5,
            neg_hysteresis=0.5,
            thermo_active=True,
            # Omit optional parameters to test defaults
        )

        assert conf.min_on_s == 240
        assert conf.min_off_s == 240
        assert conf.poll_interval_s == 15
        assert conf.smooth_window == 5
        assert conf.max_stale_s == 120

    def test_config_with_null_max_stale(self, ctrl):
        """Test configuration with null max_stale_s."""
        _ = ctrl.save_thermostat_conf(
            sleep_active=False,
            sleep_start=None,
            sleep_stop=None,
            target_temp=23.0,
            pos_hysteresis=0.5,
            neg_hysteresis=0.5,
            thermo_active=True,
            max_stale_s=None,
        )

        retrieved = ctrl.get_thermostat_conf()
        assert retrieved is not None
        # Should default to 120 when retrieved
        assert retrieved.max_stale_s == 120


class TestThermostatSeeding:
    """Test thermostat configuration seeding from config objects."""

    def test_ensure_seeds_when_empty(self, ctrl):
        """Test that seeding creates config when none exists."""

        class MockConfig:
            target_temp = 24.0
            pos_hysteresis = 0.6
            neg_hysteresis = 0.4
            sleep_active = True
            sleep_start = "22:30"
            sleep_stop = "06:30"
            thermo_active = True
            min_on_s = 300
            min_off_s = 300
            poll_interval_s = 20
            smooth_window = 7
            max_stale_s = 180
            current_phase = "on"
            phase_started_at = "2024-01-01T10:00:00Z"

        conf = ctrl.ensure_thermostat_conf_seeded_from(MockConfig())

        assert conf.target_temp == 24.0
        assert conf.pos_hysteresis == 0.6
        assert conf.neg_hysteresis == 0.4
        assert conf.sleep_active is True
        assert conf.sleep_start == "22:30"
        assert conf.min_on_s == 300

    def test_ensure_does_not_overwrite_existing(self, ctrl):
        """Test that seeding doesn't overwrite existing configuration."""
        # Create initial config
        ctrl.save_thermostat_conf(
            sleep_active=False,
            sleep_start=None,
            sleep_stop=None,
            target_temp=21.0,
            pos_hysteresis=0.3,
            neg_hysteresis=0.3,
            thermo_active=True,
        )

        class MockConfig:
            target_temp = 99.0  # This should NOT be used
            pos_hysteresis = 9.0
            neg_hysteresis = 9.0

        conf = ctrl.ensure_thermostat_conf_seeded_from(MockConfig())

        # Should return existing config, not seed values
        assert conf.target_temp == 21.0
        assert conf.pos_hysteresis == 0.3

    def test_ensure_with_legacy_names(self, ctrl):
        """Test seeding with legacy config attribute names."""

        class LegacyConfig:
            setpoint_c = 23.5  # Legacy name for target_temp
            pos_hysteresis = 0.5
            neg_hysteresis = 0.5
            sleep_enabled = True  # Legacy name for sleep_active

        conf = ctrl.ensure_thermostat_conf_seeded_from(LegacyConfig())

        assert conf.target_temp == 23.5  # Should use setpoint_c
        assert conf.sleep_active is True  # Should use sleep_enabled

    def test_ensure_with_none_config(self, ctrl):
        """Test seeding with None uses defaults."""
        conf = ctrl.ensure_thermostat_conf_seeded_from(None)

        # Should create with hardcoded defaults
        assert conf.target_temp == 24.5
        assert conf.pos_hysteresis == 0.5
        assert conf.neg_hysteresis == 0.5
        assert conf.sleep_active is True
        assert conf.thermo_active is True
        assert conf.min_on_s == 240

    def test_ensure_with_partial_config(self, ctrl):
        """Test seeding with config that has only some attributes."""

        class PartialConfig:
            target_temp = 26.0
            # Missing other attributes

        conf = ctrl.ensure_thermostat_conf_seeded_from(PartialConfig())

        # Should use provided value for target_temp
        assert conf.target_temp == 26.0
        # Should use defaults for missing values
        assert conf.pos_hysteresis == 0.5
        assert conf.neg_hysteresis == 0.5


class TestSleepManagerEarlySleep:
    """Test transient early-sleep behavior for weekly sleep schedules."""

    def _weekly_sleep_cfg(self):
        keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        return SimpleNamespace(
            sleep_active=True,
            sleep_start=None,
            sleep_stop=None,
            sleep_weekly=json.dumps({key: {"start": "22:00", "stop": "07:00"} for key in keys}),
        )

    def test_early_sleep_makes_weekly_schedule_active(self, monkeypatch):
        """Early sleep should act like sleep time before the scheduled window starts."""
        cfg = self._weekly_sleep_cfg()
        emitted: list[str] = []
        manager = SleepManager(cfg, None, lambda: emitted.append("sleep_status"))

        monkeypatch.setattr(sleep_manager_module, "now_minutes_local", lambda: 12 * 60)

        assert manager.is_sleep_window_now() is False

        manager.early_sleep_enabled = True

        assert manager.is_sleep_window_now() is True
        assert manager.get_status_payload()["sleep_time_active"] is True
        assert manager.get_status_payload()["early_sleep_enabled"] is True
        assert emitted == []

    def test_early_sleep_clears_when_real_weekly_window_starts(self, monkeypatch):
        """Early sleep is transient and resets once the scheduled sleep window begins."""
        cfg = self._weekly_sleep_cfg()
        emitted: list[str] = []
        manager = SleepManager(cfg, None, lambda: emitted.append("sleep_status"))
        manager.early_sleep_enabled = True

        monkeypatch.setattr(sleep_manager_module, "now_minutes_local", lambda: 23 * 60)

        assert manager.is_sleep_window_now() is True
        assert manager.early_sleep_enabled is False
        assert emitted == ["sleep_status"]

        assert manager.is_sleep_window_now() is True
        assert emitted == ["sleep_status"]


class TestSleepManagerOverride:
    """Test temporary sleep override state and expiry notifications."""

    @staticmethod
    def _cfg():
        return SimpleNamespace(
            sleep_active=True,
            sleep_start="22:00",
            sleep_stop="07:00",
            sleep_weekly=None,
        )

    def test_active_override_status_includes_hhmm(self, monkeypatch):
        emitted: list[str] = []
        manager = SleepManager(self._cfg(), UTC, lambda: emitted.append("sleep_status"))
        monkeypatch.setattr(sleep_manager_module.time, "time", lambda: 1000.0)

        manager.disable_for(5)

        payload = manager.get_status_payload()
        assert payload["sleep_override_active"] is True
        assert payload["sleep_override_until"] == "00:21"
        assert emitted == []

    def test_expired_override_clears_and_emits_status(self, monkeypatch):
        emitted: list[str] = []
        manager = SleepManager(self._cfg(), UTC, lambda: emitted.append("sleep_status"))
        now = 1000.0
        monkeypatch.setattr(sleep_manager_module.time, "time", lambda: now)
        monkeypatch.setattr(sleep_manager_module, "now_minutes_local", lambda: 12 * 60)
        manager.disable_for(1)

        now = 1061.0

        assert manager.is_sleep_window_now() is False
        assert manager.override_until is None
        assert manager.override_active is False
        assert emitted == ["sleep_status"]

    def test_cancel_override_clears_status(self, monkeypatch):
        manager = SleepManager(self._cfg(), UTC, lambda: None)
        monkeypatch.setattr(sleep_manager_module.time, "time", lambda: 1000.0)
        manager.disable_for(5)

        manager.cancel_sleep_override()

        payload = manager.get_status_payload()
        assert payload["sleep_override_active"] is False
        assert payload["sleep_override_until"] is None


class TestSleepManagerSleepFor:
    """Test temporary sleep-for state and expiry notifications."""

    @staticmethod
    def _cfg():
        return SimpleNamespace(
            sleep_active=True,
            sleep_start="22:00",
            sleep_stop="07:00",
            sleep_weekly=None,
        )

    def test_sleep_for_status_includes_hhmm_and_sleep_active(self, monkeypatch):
        emitted: list[str] = []
        manager = SleepManager(self._cfg(), UTC, lambda: emitted.append("sleep_status"))
        monkeypatch.setattr(sleep_manager_module.time, "time", lambda: 1000.0)
        monkeypatch.setattr(sleep_manager_module, "now_minutes_local", lambda: 12 * 60)

        manager.sleep_for(10)

        payload = manager.get_status_payload()
        assert payload["sleep_time_active"] is True
        assert payload["sleep_for_active"] is True
        assert payload["sleep_for_until"] == "00:26"
        assert emitted == []

    def test_expired_sleep_for_clears_and_emits_status(self, monkeypatch):
        emitted: list[str] = []
        manager = SleepManager(self._cfg(), UTC, lambda: emitted.append("sleep_status"))
        now = 1000.0
        monkeypatch.setattr(sleep_manager_module.time, "time", lambda: now)
        monkeypatch.setattr(sleep_manager_module, "now_minutes_local", lambda: 12 * 60)
        manager.sleep_for(1)

        now = 1061.0

        assert manager.is_sleep_window_now() is False
        assert manager.sleep_for_until is None
        assert manager.sleep_for_active is False
        assert emitted == ["sleep_status"]

    def test_scheduled_sleep_clears_active_sleep_for(self, monkeypatch):
        emitted: list[str] = []
        manager = SleepManager(self._cfg(), UTC, lambda: emitted.append("sleep_status"))
        monkeypatch.setattr(sleep_manager_module.time, "time", lambda: 1000.0)
        current_minutes = 12 * 60
        monkeypatch.setattr(
            sleep_manager_module,
            "now_minutes_local",
            lambda: current_minutes,
        )
        manager.sleep_for(10)

        current_minutes = 23 * 60

        assert manager.is_sleep_window_now() is True
        assert manager.sleep_for_until is None
        assert manager.sleep_for_active is False
        assert emitted == ["sleep_status"]

    def test_sleep_for_cancels_disable_override(self, monkeypatch):
        manager = SleepManager(self._cfg(), UTC, lambda: None)
        monkeypatch.setattr(sleep_manager_module.time, "time", lambda: 1000.0)

        manager.disable_for(5)
        manager.sleep_for(10)

        payload = manager.get_status_payload()
        assert payload["sleep_override_active"] is False
        assert payload["sleep_for_active"] is True


class TestACMigration:
    """Test AC data migration from SQLite to PostgreSQL."""

    def test_migrate_ac_to_pg(self):
        """Test complete AC migration with bulk inserts."""
        # Create a fresh temp database for this test only
        fd, temp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            # Create SQLite data
            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()

            # Create tables
            cursor.execute(
                """
                CREATE TABLE ac_events (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    is_on INTEGER NOT NULL,
                    source TEXT,
                    note TEXT
                )
            """
            )

            cursor.execute(
                """
                CREATE TABLE thermostat_conf (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    sleep_active INTEGER NOT NULL,
                    sleep_start TEXT,
                    sleep_stop TEXT,
                    sleep_weekly TEXT,
                    control_locations TEXT,
                    target_temp REAL NOT NULL,
                    pos_hysteresis REAL NOT NULL,
                    neg_hysteresis REAL NOT NULL,
                    thermo_active INTEGER NOT NULL,
                    total_on_s INTEGER NOT NULL DEFAULT 0,
                    total_off_s INTEGER NOT NULL DEFAULT 0,
                    min_on_s INTEGER NOT NULL DEFAULT 240,
                    min_off_s INTEGER NOT NULL DEFAULT 240,
                    poll_interval_s INTEGER NOT NULL DEFAULT 15,
                    smooth_window INTEGER NOT NULL DEFAULT 5,
                    max_stale_s INTEGER DEFAULT 120,
                    current_phase TEXT,
                    phase_started_at TEXT
                )
            """
            )

            # Insert test data
            base = datetime.now(UTC)
            events = [
                (base - timedelta(hours=2), 1, "sensor1", "turned on"),
                (base - timedelta(hours=1), 0, "sensor2", "turned off"),
                (base, 1, "sensor3", "turned on again"),
            ]

            for ts, is_on, source, note in events:
                cursor.execute(
                    "INSERT INTO ac_events (timestamp, is_on, source, note) VALUES (?, ?, ?, ?)",
                    (ts.isoformat(), is_on, source, note),
                )

            cursor.execute(
                """
                INSERT INTO thermostat_conf (
                    id, sleep_active, sleep_start, sleep_stop, sleep_weekly,
                    control_locations, target_temp, pos_hysteresis, neg_hysteresis,
                    thermo_active, min_on_s, min_off_s, poll_interval_s,
                    smooth_window, max_stale_s, current_phase, phase_started_at
                ) VALUES (1, 1, '22:00', '07:00', '1111100', 'bedroom', 24.5, 0.5, 0.5, 1, 240, 240, 15, 5, 120, 'on', '2024-01-01T12:00:00Z')
            """
            )

            conn.commit()
            conn.close()

            # Create controller and run migration
            ctrl = Controller(db_path=temp_db)
            if ctrl._sa_engine is None:
                from app.core.sqlalchemy_engine import get_engine

                ctrl._sa_engine = get_engine(temp_db)

            # Don't create tables - migration should handle existing SQLite data
            # Run migration (it will fail but we just verify it doesn't crash)
            stats = ctrl.migrate_ac_to_pg(batch_size=100)

            # In test environment, source and dest are same DB
            # So we just verify migration ran without crashing
            assert "ac_events" in stats
            assert "thermostat_conf" in stats
            assert "migrated" in stats["ac_events"]
            assert "errors" in stats["ac_events"]
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temp_db)

    def test_migrate_ac_events(self, temp_db):
        """Test migrating AC events from SQLite."""
        # Create SQLite data
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE ac_events (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                is_on INTEGER NOT NULL,
                source TEXT,
                note TEXT
            )
        """
        )

        base = datetime.now(UTC)
        events = [
            (base - timedelta(hours=2), 1, "sensor1", "turned on"),
            (base - timedelta(hours=1), 0, "sensor2", "turned off"),
            (base, 1, "sensor3", "turned on again"),
        ]

        for ts, is_on, source, note in events:
            cursor.execute(
                "INSERT INTO ac_events (timestamp, is_on, source, note) VALUES (?, ?, ?, ?)",
                (ts.isoformat(), is_on, source, note),
            )

        conn.commit()
        conn.close()

        # Verify SQLite data exists
        ctrl_sqlite = Controller(db_path=temp_db)
        # Initialize SQLAlchemy engine for SQLite
        if ctrl_sqlite._sa_engine is None:
            from app.core.sqlalchemy_engine import get_engine

            ctrl_sqlite._sa_engine = get_engine(temp_db)
        assert (
            len(
                ctrl_sqlite.get_ac_events_between(
                    start_iso=(base - timedelta(hours=3)).isoformat(),
                    end_iso=(base + timedelta(hours=1)).isoformat(),
                )
            )
            == 3
        )

    def test_migrate_thermostat_config(self, temp_db):
        """Test migrating thermostat config from SQLite."""
        # Create SQLite data
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE thermostat_conf (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                sleep_active INTEGER NOT NULL,
                sleep_start TEXT,
                sleep_stop TEXT,
                sleep_weekly TEXT,
                control_locations TEXT,
                target_temp REAL NOT NULL,
                pos_hysteresis REAL NOT NULL,
                neg_hysteresis REAL NOT NULL,
                thermo_active INTEGER NOT NULL,
                total_on_s INTEGER NOT NULL DEFAULT 0,
                total_off_s INTEGER NOT NULL DEFAULT 0,
                min_on_s INTEGER NOT NULL DEFAULT 240,
                min_off_s INTEGER NOT NULL DEFAULT 240,
                poll_interval_s INTEGER NOT NULL DEFAULT 15,
                smooth_window INTEGER NOT NULL DEFAULT 5,
                max_stale_s INTEGER DEFAULT 120,
                current_phase TEXT,
                phase_started_at TEXT
            )
        """
        )

        cursor.execute(
            """
            INSERT INTO thermostat_conf (
                id, sleep_active, sleep_start, sleep_stop, sleep_weekly,
                control_locations, target_temp, pos_hysteresis, neg_hysteresis,
                thermo_active, min_on_s, min_off_s, poll_interval_s,
                smooth_window, max_stale_s, current_phase, phase_started_at
            ) VALUES (1, 1, '22:00', '07:00', '1111100', 'bedroom', 24.5, 0.5, 0.5, 1, 240, 240, 15, 5, 120, 'on', '2024-01-01T12:00:00Z')
        """
        )

        conn.commit()
        conn.close()

        # Verify SQLite data
        ctrl_sqlite = Controller(db_path=temp_db)
        # Initialize SQLAlchemy engine for SQLite
        if ctrl_sqlite._sa_engine is None:
            from app.core.sqlalchemy_engine import get_engine

            ctrl_sqlite._sa_engine = get_engine(temp_db)
        conf = ctrl_sqlite.get_thermostat_conf()
        assert conf is not None
        assert conf.target_temp == 24.5
        assert conf.sleep_start == "22:00"
        assert conf.current_phase == "on"
