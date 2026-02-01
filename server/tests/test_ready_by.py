"""Tests for car heater ready-by controller methods."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile

import pytest

from app.core.controller import Controller
from app.core.models import CarHeaterReadyByConfig, CarHeaterReadyByState


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    with contextlib.suppress(Exception):
        os.unlink(path)


@pytest.fixture
def controller(temp_db):
    """Create a controller with temporary database."""
    ctrl = Controller(db_path=temp_db)
    # Ensure SA engine is initialized
    if ctrl._sa_engine is None:
        from app.core.sqlalchemy_engine import get_engine

        ctrl._sa_engine = get_engine(temp_db)

    # Create tables using SQLAlchemy metadata
    from app.core.schema import metadata

    metadata.create_all(ctrl._sa_engine)

    yield ctrl
    # Cleanup
    if ctrl._sa_engine:
        ctrl._sa_engine.dispose()


class TestReadyByState:
    """Test car heater ready-by state operations."""

    def test_get_ready_by_state_none(self, controller):
        """Test fetching state when none exists."""
        state = controller.get_ready_by_state()
        assert state is None

    def test_save_and_get_ready_by_state(self, controller):
        """Test saving and retrieving state."""
        state_data = {
            "enabled": True,
            "target_time": "08:00",
            "weekdays": [1, 2, 3, 4, 5],
        }
        state = CarHeaterReadyByState(
            id=1,
            state_json=json.dumps(state_data),
            updated_ts="2026-01-01T12:00:00+02:00",
        )

        controller.save_ready_by_state(state)

        retrieved = controller.get_ready_by_state()
        assert retrieved is not None
        assert retrieved.id == 1
        assert retrieved.state_json == json.dumps(state_data)
        assert retrieved.updated_ts == "2026-01-01T12:00:00+02:00"

    def test_save_ready_by_state_upsert(self, controller):
        """Test that saving state upserts (updates existing record)."""
        # Save initial state
        state1 = CarHeaterReadyByState(
            id=1,
            state_json=json.dumps({"enabled": False}),
            updated_ts="2026-01-01T12:00:00+02:00",
        )
        controller.save_ready_by_state(state1)

        # Update state
        state2 = CarHeaterReadyByState(
            id=1,
            state_json=json.dumps({"enabled": True}),
            updated_ts="2026-01-01T13:00:00+02:00",
        )
        controller.save_ready_by_state(state2)

        # Should have updated, not created new
        retrieved = controller.get_ready_by_state()
        assert retrieved is not None
        assert retrieved.state_json == json.dumps({"enabled": True})
        assert retrieved.updated_ts == "2026-01-01T13:00:00+02:00"

    def test_save_ready_by_state_complex_json(self, controller):
        """Test saving complex JSON state."""
        complex_state = {
            "enabled": True,
            "schedules": [
                {"day": "monday", "time": "08:00"},
                {"day": "tuesday", "time": "07:30"},
            ],
            "settings": {
                "preheat_duration": 30,
                "temperature": 21.5,
            },
        }
        state = CarHeaterReadyByState(
            id=1,
            state_json=json.dumps(complex_state),
            updated_ts="2026-01-01T12:00:00+02:00",
        )

        controller.save_ready_by_state(state)

        retrieved = controller.get_ready_by_state()
        assert retrieved is not None
        assert json.loads(retrieved.state_json) == complex_state


class TestReadyByConfig:
    """Test car heater ready-by config operations."""

    def test_get_ready_by_config_none(self, controller):
        """Test fetching config when none exists."""
        config = controller.get_ready_by_config()
        assert config is None

    def test_save_and_get_ready_by_config(self, controller):
        """Test saving and retrieving config."""
        config_data = {
            "default_duration": 45,
            "max_duration": 120,
            "temperature_threshold": -5,
        }
        config = CarHeaterReadyByConfig(
            id=1,
            config_json=json.dumps(config_data),
            updated_ts="2026-01-01T12:00:00+02:00",
        )

        controller.save_ready_by_config(config)

        retrieved = controller.get_ready_by_config()
        assert retrieved is not None
        assert retrieved.id == 1
        assert retrieved.config_json == json.dumps(config_data)
        assert retrieved.updated_ts == "2026-01-01T12:00:00+02:00"

    def test_save_ready_by_config_upsert(self, controller):
        """Test that saving config upserts (updates existing record)."""
        # Save initial config
        config1 = CarHeaterReadyByConfig(
            id=1,
            config_json=json.dumps({"duration": 30}),
            updated_ts="2026-01-01T12:00:00+02:00",
        )
        controller.save_ready_by_config(config1)

        # Update config
        config2 = CarHeaterReadyByConfig(
            id=1,
            config_json=json.dumps({"duration": 60}),
            updated_ts="2026-01-01T13:00:00+02:00",
        )
        controller.save_ready_by_config(config2)

        # Should have updated, not created new
        retrieved = controller.get_ready_by_config()
        assert retrieved is not None
        assert retrieved.config_json == json.dumps({"duration": 60})
        assert retrieved.updated_ts == "2026-01-01T13:00:00+02:00"

    def test_save_ready_by_config_complex_json(self, controller):
        """Test saving complex JSON config."""
        complex_config = {
            "presets": [
                {"name": "Quick", "duration": 30},
                {"name": "Standard", "duration": 60},
                {"name": "Extended", "duration": 120},
            ],
            "auto_adjust": {
                "enabled": True,
                "min_temp": -10,
                "max_temp": 5,
            },
        }
        config = CarHeaterReadyByConfig(
            id=1,
            config_json=json.dumps(complex_config),
            updated_ts="2026-01-01T12:00:00+02:00",
        )

        controller.save_ready_by_config(config)

        retrieved = controller.get_ready_by_config()
        assert retrieved is not None
        assert json.loads(retrieved.config_json) == complex_config


class TestStateAndConfigTogether:
    """Test using both state and config together."""

    def test_save_both_state_and_config(self, controller):
        """Test saving both state and config."""
        state = CarHeaterReadyByState(
            id=1,
            state_json=json.dumps({"enabled": True}),
            updated_ts="2026-01-01T12:00:00+02:00",
        )
        config = CarHeaterReadyByConfig(
            id=1,
            config_json=json.dumps({"duration": 45}),
            updated_ts="2026-01-01T12:00:00+02:00",
        )

        controller.save_ready_by_state(state)
        controller.save_ready_by_config(config)

        retrieved_state = controller.get_ready_by_state()
        retrieved_config = controller.get_ready_by_config()

        assert retrieved_state is not None
        assert retrieved_config is not None
        assert json.loads(retrieved_state.state_json) == {"enabled": True}
        assert json.loads(retrieved_config.config_json) == {"duration": 45}


class TestMigration:
    """Test ready-by data migration.

    Note: These tests have limitations because in the test environment,
    the source (legacy SQLite via self.db) and destination (SQLAlchemy)
    point to the same database file. In production, these would be separate
    databases. These tests verify that the migration function runs.
    """

    def test_migrate_runs_successfully(self, controller):
        """Test migration function runs without errors."""
        stats = controller.migrate_ready_by_to_pg()

        # Verify structure
        assert "ready_by_state" in stats
        assert "ready_by_config" in stats

        # Verify completion
        for table_name in ["ready_by_state", "ready_by_config"]:
            assert "migrated" in stats[table_name]
            assert "errors" in stats[table_name]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
