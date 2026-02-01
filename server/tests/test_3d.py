"""Tests for ThreeD controller methods (3D printer management)."""

from __future__ import annotations

import contextlib
import os
import tempfile

import pytest

from app.core.controller import Controller


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

    # Initialize singleton records (use INSERT OR IGNORE to avoid conflicts)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from app.core.schema import status, timelapse_conf

    with ctrl._sa_engine.begin() as conn:
        # Insert status singleton (ignore if exists)
        stmt = sqlite_insert(status).values(
            id=1, timestamp="2026-01-01T00:00:00+02:00", status="idle"
        )
        stmt = stmt.on_conflict_do_nothing()
        conn.execute(stmt)

        # Insert timelapse_conf singleton (ignore if exists)
        stmt = sqlite_insert(timelapse_conf).values(
            id=1, image_delay=30, temphum_delay=60, status_delay=15
        )
        stmt = stmt.on_conflict_do_nothing()
        conn.execute(stmt)

    yield ctrl
    # Cleanup
    if ctrl._sa_engine:
        ctrl._sa_engine.dispose()


class TestStatusOperations:
    """Test 3D printer status operations."""

    def test_get_last_3d_status(self, controller):
        """Test fetching current status."""
        status = controller.get_last_3d_status()
        assert status is not None
        assert status.id == 1
        assert status.status is not None  # May be set by database initialization
        assert status.timestamp is not None

    def test_update_3d_status(self, controller):
        """Test updating status."""
        status = controller.update_3d_status("printing")
        assert status.id == 1
        assert status.status == "printing"
        assert status.timestamp is not None

        # Verify it persisted
        fetched = controller.get_last_3d_status()
        assert fetched is not None
        assert fetched.status == "printing"

    def test_update_status_multiple_times(self, controller):
        """Test updating status multiple times."""
        status1 = controller.update_3d_status("printing")
        assert status1.status == "printing"

        status2 = controller.update_3d_status("paused")
        assert status2.status == "paused"
        assert status2.id == status1.id  # Same record (singleton)

        status3 = controller.update_3d_status("completed")
        assert status3.status == "completed"


class TestImageOperations:
    """Test 3D printer image operations."""

    def test_record_image(self, controller):
        """Test recording an image."""
        image_data = "base64encodedimage123"
        img = controller.record_image(image_data)
        assert img.id is not None
        assert img.image == image_data
        assert img.timestamp is not None

    def test_get_last_image(self, controller):
        """Test fetching most recent image."""
        # Initially no images
        img = controller.get_last_image()
        assert img is None

        # Record an image
        controller.record_image("image1")
        img = controller.get_last_image()
        assert img is not None
        assert img.image == "image1"

    def test_get_last_image_returns_most_recent(self, controller):
        """Test that get_last_image returns the most recent one."""
        controller.record_image("image1")
        controller.record_image("image2")
        img3 = controller.record_image("image3")

        last = controller.get_last_image()
        assert last is not None
        assert last.id == img3.id
        assert last.image == "image3"

    def test_record_multiple_images(self, controller):
        """Test recording multiple images."""
        img1 = controller.record_image("image1")
        img2 = controller.record_image("image2")
        img3 = controller.record_image("image3")

        # IDs should increment
        assert img2.id > img1.id
        assert img3.id > img2.id

        # Last image should be img3
        last = controller.get_last_image()
        assert last.id == img3.id


class TestTimelapseConfiguration:
    """Test timelapse configuration operations."""

    def test_get_timelapse_conf(self, controller):
        """Test fetching timelapse configuration."""
        conf = controller.get_timelapse_conf()
        assert conf is not None
        assert conf.id == 1
        assert conf.image_delay == 30
        assert conf.temphum_delay == 60
        assert conf.status_delay == 15

    def test_update_timelapse_conf(self, controller):
        """Test updating timelapse configuration."""
        controller.update_timelapse_conf(image_delay=45, temphum_delay=90, status_delay=20)

        conf = controller.get_timelapse_conf()
        assert conf is not None
        assert conf.image_delay == 45
        assert conf.temphum_delay == 90
        assert conf.status_delay == 20

    def test_update_timelapse_conf_partial(self, controller):
        """Test updating some timelapse settings."""
        # Update some values
        controller.update_timelapse_conf(image_delay=10, temphum_delay=60, status_delay=15)

        conf = controller.get_timelapse_conf()
        assert conf is not None
        assert conf.image_delay == 10
        assert conf.temphum_delay == 60  # Unchanged
        assert conf.status_delay == 15  # Unchanged


class TestGcodeCommands:
    """Test G-code command operations."""

    def test_record_gcode_command(self, controller):
        """Test recording a G-code command."""
        controller.record_gcode_command("G28")  # Should not raise

    def test_get_all_gcode_commands_empty(self, controller):
        """Test fetching commands when none exist."""
        commands = controller.get_all_gcode_commands()
        assert commands == []

    def test_get_all_gcode_commands(self, controller):
        """Test fetching all commands."""
        controller.record_gcode_command("G28")
        controller.record_gcode_command("G1 X10 Y20")
        controller.record_gcode_command("M104 S200")

        commands = controller.get_all_gcode_commands()
        assert len(commands) == 3
        assert "G28" in commands
        assert "G1 X10 Y20" in commands
        assert "M104 S200" in commands

    def test_get_all_gcode_commands_deduplicates(self, controller):
        """Test that duplicate commands are deduplicated."""
        controller.record_gcode_command("G28")
        controller.record_gcode_command("G1 X10")
        controller.record_gcode_command("G28")  # Duplicate
        controller.record_gcode_command("G1 X10")  # Duplicate

        commands = controller.get_all_gcode_commands()
        assert len(commands) == 2
        assert "G28" in commands
        assert "G1 X10" in commands

    def test_record_many_gcode_commands(self, controller):
        """Test recording many commands."""
        for i in range(10):
            controller.record_gcode_command(f"G1 X{i} Y{i}")

        commands = controller.get_all_gcode_commands()
        assert len(commands) == 10


class TestMigration:
    """Test 3D data migration.

    Note: These tests have limitations because in the test environment,
    the source (legacy SQLite via self.db) and destination (SQLAlchemy)
    point to the same database file. In production, these would be separate
    databases. These tests verify that the migration function runs.
    """

    def test_migrate_runs_successfully(self, controller):
        """Test migration function runs without errors."""
        stats = controller.migrate_3d_to_pg()

        # Verify structure
        assert "status" in stats
        assert "images" in stats
        assert "timelapse_conf" in stats
        assert "gcode_commands" in stats

        # Verify completion
        for table_name in ["status", "images", "timelapse_conf", "gcode_commands"]:
            assert "migrated" in stats[table_name]
            assert "errors" in stats[table_name]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
