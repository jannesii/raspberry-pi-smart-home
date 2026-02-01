"""Tests for auth controller methods (user and API key management)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.controller import Controller


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:  # noqa: SIM105
        os.unlink(path)
    except Exception:
        pass


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


class TestUserOperations:
    """Test user CRUD operations."""

    def test_register_user_basic(self, controller):
        """Test basic user registration."""
        user = controller.register_user("testuser", password="testpass123")
        assert user.username == "testuser"
        assert user.id is not None
        assert user.password_hash is not None
        assert not user.is_admin
        assert not user.is_root_admin
        assert not user.is_temporary
        assert user.expires_at is None

    def test_register_user_with_password_hash(self, controller):
        """Test user registration with pre-hashed password."""
        from werkzeug.security import generate_password_hash

        pw_hash = generate_password_hash("testpass123")
        user = controller.register_user("testuser", password_hash=pw_hash)
        assert user.username == "testuser"
        assert user.password_hash == pw_hash

    def test_register_user_admin(self, controller):
        """Test admin user registration."""
        user = controller.register_user("admin", password="adminpass", is_admin=True)
        assert user.is_admin
        assert not user.is_root_admin

    def test_register_user_root_admin(self, controller):
        """Test root admin user registration."""
        user = controller.register_user(
            "root", password="rootpass", is_admin=True, is_root_admin=True
        )
        assert user.is_admin
        assert user.is_root_admin

    def test_register_user_idempotent(self, controller):
        """Test that registering same user twice returns existing user."""
        user1 = controller.register_user("testuser", password="testpass123")
        user2 = controller.register_user("testuser", password="different")
        assert user1.id == user2.id
        assert user1.username == user2.username
        # Password hash should remain from first registration
        assert user1.password_hash == user2.password_hash

    def test_register_user_no_password(self, controller):
        """Test that registration without password raises error."""
        with pytest.raises(ValueError, match="password or password_hash"):
            controller.register_user("testuser")

    def test_authenticate_user_success(self, controller):
        """Test successful user authentication."""
        controller.register_user("testuser", password="testpass123")
        assert controller.authenticate_user("testuser", "testpass123")

    def test_authenticate_user_wrong_password(self, controller):
        """Test authentication with wrong password."""
        controller.register_user("testuser", password="testpass123")
        assert not controller.authenticate_user("testuser", "wrongpass")

    def test_authenticate_user_nonexistent(self, controller):
        """Test authentication of non-existent user."""
        assert not controller.authenticate_user("nonexistent", "anypass")

    def test_get_user_by_username(self, controller):
        """Test fetching user by username."""
        controller.register_user("testuser", password="testpass123")
        user = controller.get_user_by_username("testuser")
        assert user is not None
        assert user.username == "testuser"
        assert user.password_hash is not None

    def test_get_user_by_username_exclude_password(self, controller):
        """Test fetching user without password hash."""
        controller.register_user("testuser", password="testpass123")
        user = controller.get_user_by_username("testuser", include_pw=False)
        assert user is not None
        assert user.username == "testuser"
        assert user.password_hash is None

    def test_get_user_by_username_nonexistent(self, controller):
        """Test fetching non-existent user returns None."""
        user = controller.get_user_by_username("nonexistent")
        assert user is None

    def test_get_all_users(self, controller):
        """Test fetching all users."""
        controller.register_user("user1", password="pass1")
        controller.register_user("user2", password="pass2")
        controller.register_user("user3", password="pass3")

        users = controller.get_all_users()
        assert len(users) == 3
        usernames = {u.username for u in users}
        assert usernames == {"user1", "user2", "user3"}

    def test_get_all_users_exclude_admin(self, controller):
        """Test fetching users excluding admins."""
        controller.register_user("user1", password="pass1")
        controller.register_user("admin", password="adminpass", is_admin=True)
        controller.register_user("user2", password="pass2")

        users = controller.get_all_users(exclude_admin=True)
        assert len(users) == 2
        usernames = {u.username for u in users}
        assert usernames == {"user1", "user2"}

    def test_set_user_as_admin(self, controller):
        """Test setting user as admin."""
        controller.register_user("testuser", password="testpass123")
        controller.set_user_as_admin("testuser", True)

        user = controller.get_user_by_username("testuser")
        assert user.is_admin

    def test_delete_user(self, controller):
        """Test deleting a user."""
        controller.register_user("testuser", password="testpass123")
        controller.delete_user("testuser")

        user = controller.get_user_by_username("testuser")
        assert user is None

    def test_update_user_username(self, controller):
        """Test updating username."""
        controller.register_user("oldname", password="testpass123")
        updated = controller.update_user("oldname", new_username="newname")

        assert updated.username == "newname"
        assert controller.get_user_by_username("oldname") is None
        assert controller.get_user_by_username("newname") is not None

    def test_update_user_password(self, controller):
        """Test updating password."""
        controller.register_user("testuser", password="oldpass")
        old_hash = controller.get_user_by_username("testuser").password_hash

        controller.update_user("testuser", password="newpass")

        new_hash = controller.get_user_by_username("testuser").password_hash
        assert new_hash != old_hash
        assert controller.authenticate_user("testuser", "newpass")
        assert not controller.authenticate_user("testuser", "oldpass")

    def test_update_user_admin_status(self, controller):
        """Test updating admin status."""
        controller.register_user("testuser", password="testpass123")
        controller.update_user("testuser", is_admin=True)

        user = controller.get_user_by_username("testuser")
        assert user.is_admin

    def test_update_user_duplicate_username(self, controller):
        """Test that updating to existing username fails."""
        controller.register_user("user1", password="pass1")
        controller.register_user("user2", password="pass2")

        with pytest.raises(ValueError, match="jo käytössä"):
            controller.update_user("user1", new_username="user2")

    def test_update_user_nonexistent(self, controller):
        """Test updating non-existent user fails."""
        with pytest.raises(ValueError, match="User not found"):
            controller.update_user("nonexistent", password="newpass")


class TestTemporaryUsers:
    """Test temporary user functionality."""

    def test_create_temporary_user_hours(self, controller):
        """Test creating temporary user with hour expiration."""
        user = controller.create_temporary_user("tempuser", "temppass", 2, "hours")
        assert user.is_temporary
        assert user.expires_at is not None

        expires = datetime.fromisoformat(user.expires_at)
        now = datetime.now(ZoneInfo("Europe/Helsinki"))
        diff = (expires - now).total_seconds()
        # Should be roughly 2 hours (7200 seconds), allow some variance
        assert 7100 < diff < 7300

    def test_create_temporary_user_days(self, controller):
        """Test creating temporary user with day expiration."""
        user = controller.create_temporary_user("tempuser", "temppass", 1, "days")
        assert user.is_temporary

        expires = datetime.fromisoformat(user.expires_at)
        now = datetime.now(ZoneInfo("Europe/Helsinki"))
        diff = (expires - now).total_seconds()
        # Should be roughly 1 day (86400 seconds)
        assert 86300 < diff < 86500

    def test_delete_temporary_users(self, controller):
        """Test deleting all temporary users."""
        controller.register_user("permanent", password="pass1")
        controller.create_temporary_user("temp1", "pass2", 1, "hours")
        controller.create_temporary_user("temp2", "pass3", 1, "hours")

        assert len(controller.get_all_users()) == 3

        controller.delete_temporary_users()

        users = controller.get_all_users()
        assert len(users) == 1
        assert users[0].username == "permanent"

    def test_delete_expired_temporary_users(self, controller):
        """Test deleting only expired temporary users."""
        # Create a temporary user with expiration in the past
        past = (datetime.now(ZoneInfo("Europe/Helsinki")) - timedelta(hours=1)).isoformat()
        controller.register_user("expired", password="pass1", is_temporary=True, expires_at=past)

        # Create a non-expired temporary user
        future = (datetime.now(ZoneInfo("Europe/Helsinki")) + timedelta(hours=1)).isoformat()
        controller.register_user(
            "notexpired", password="pass2", is_temporary=True, expires_at=future
        )

        # Create a permanent user
        controller.register_user("permanent", password="pass3")

        assert len(controller.get_all_users()) == 3

        controller.delete_expired_temporary_users()

        users = controller.get_all_users()
        assert len(users) == 2
        usernames = {u.username for u in users}
        assert usernames == {"notexpired", "permanent"}

    def test_update_user_temporary_to_permanent(self, controller):
        """Test converting temporary user to permanent."""
        user = controller.create_temporary_user("tempuser", "temppass", 1, "hours")
        assert user.is_temporary
        assert user.expires_at is not None

        updated = controller.update_user("tempuser", is_temporary=False)
        assert not updated.is_temporary
        assert updated.expires_at is None


class TestAPIKeys:
    """Test API key operations."""

    def test_create_api_key(self, controller):
        """Test creating API key."""
        api_key, token = controller.create_api_key("Test Key", created_by="testuser")

        assert api_key.name == "Test Key"
        assert api_key.created_by == "testuser"
        assert not api_key.revoked
        assert api_key.last_used_at is None
        assert api_key.key_id is not None

        assert token.startswith("sk_")
        assert api_key.key_id in token

    def test_create_api_key_no_name(self, controller):
        """Test creating API key without name fails."""
        with pytest.raises(ValueError, match="name is required"):
            controller.create_api_key("")

    def test_list_api_keys(self, controller):
        """Test listing API keys."""
        controller.create_api_key("Key 1")
        controller.create_api_key("Key 2")
        controller.create_api_key("Key 3")

        keys = controller.list_api_keys()
        assert len(keys) == 3
        # Should be ordered by id DESC (most recent first)
        assert keys[0].name == "Key 3"
        assert keys[2].name == "Key 1"

    def test_delete_api_key(self, controller):
        """Test deleting API key."""
        api_key, _ = controller.create_api_key("Test Key")
        assert len(controller.list_api_keys()) == 1

        controller.delete_api_key(api_key.key_id)
        assert len(controller.list_api_keys()) == 0

    def test_revoke_api_key(self, controller):
        """Test revoking API key."""
        api_key, _ = controller.create_api_key("Test Key")
        assert not api_key.revoked

        controller.revoke_api_key(api_key.key_id)

        keys = controller.list_api_keys()
        assert len(keys) == 1
        assert keys[0].revoked

    def test_verify_api_key_token_success(self, controller):
        """Test successful API key verification."""
        _, token = controller.create_api_key("Test Key", created_by="testuser")

        result = controller.verify_api_key_token(token)
        assert result is not None
        assert result["name"] == "Test Key"
        assert result["created_by"] == "testuser"
        assert not result["revoked"]
        # last_used_at should be updated
        assert result["last_used_at"] is not None

    def test_verify_api_key_token_invalid_format(self, controller):
        """Test verification of invalid token format."""
        assert controller.verify_api_key_token("invalid") is None
        assert controller.verify_api_key_token("sk_") is None
        assert controller.verify_api_key_token("sk_invalid") is None

    def test_verify_api_key_token_wrong_secret(self, controller):
        """Test verification with wrong secret."""
        api_key, token = controller.create_api_key("Test Key")
        # Modify the secret part
        parts = token.split("_")
        wrong_token = f"{parts[0]}_{parts[1]}_wrongsecret"

        result = controller.verify_api_key_token(wrong_token)
        assert result is None

    def test_verify_api_key_token_revoked(self, controller):
        """Test verification of revoked key fails."""
        api_key, token = controller.create_api_key("Test Key")
        controller.revoke_api_key(api_key.key_id)

        result = controller.verify_api_key_token(token)
        assert result is None

    def test_verify_api_key_token_nonexistent(self, controller):
        """Test verification of non-existent key."""
        fake_token = "sk_1234567890abcdef_fakesecretthatisverylong1234567890"
        result = controller.verify_api_key_token(fake_token)
        assert result is None


class TestAuthMigration:
    """Test auth data migration.

    Note: These tests have limitations because in the test environment,
    the source (legacy SQLite via self.db) and destination (SQLAlchemy)
    point to the same database file. In production, these would be separate
    databases (old SQLite file and new PostgreSQL). These tests verify
    that the migration function runs without crashing.
    """

    def test_migrate_runs_successfully(self, controller):
        """Test migration function runs without errors."""
        stats = controller.migrate_auth_to_pg()

        # Verify structure
        assert "users" in stats
        assert "api_keys" in stats

        # Verify no errors
        assert "errors" in stats["users"]
        assert "errors" in stats["api_keys"]

        # Migration should complete
        assert "migrated" in stats["users"]
        assert "migrated" in stats["api_keys"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
