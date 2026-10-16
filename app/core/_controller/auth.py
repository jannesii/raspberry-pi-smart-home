from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

from flask_login import current_user
from sqlalchemy import Engine, delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from ..models import ApiKey, User
from ..schema import api_keys, users

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class AuthMixin:
    # --- User operations ---
    def register_user(
        self,
        username: str,
        password: str | None = None,
        password_hash: str | None = None,
        is_admin: bool = False,
        is_root_admin: bool = False,
        is_temporary: bool = False,
        expires_at: str | None = None,
    ) -> User:
        """
        Creates the user if it doesn't exist, or returns the existing one.
        Uses INSERT ... ON CONFLICT to avoid UNIQUE errors, then SELECT to fetch.
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("Registering user: %s", username)

        # 1) Hash the password up front
        if password_hash is None and password:
            pw_hash = generate_password_hash(password)
        elif password is None and password_hash is None:
            raise ValueError("Either password or password_hash must be provided")
        else:
            pw_hash = password_hash

        # 2) Try to insert; if username exists, this is a no-op
        try:
            stmt = (
                pg_insert(users)
                .values(
                    username=username,
                    password_hash=pw_hash,
                    is_admin=is_admin,
                    is_root_admin=is_root_admin,
                    is_temporary=is_temporary,
                    expires_at=expires_at,
                )
                .on_conflict_do_nothing(index_elements=[users.c.username])
            )

            with sa_engine.begin() as conn:
                result = conn.execute(stmt)
                if result.rowcount == 1:
                    logger.debug("User '%s' created successfully", username)
                else:
                    logger.debug("User '%s' already exists, skipping INSERT", username)

            # 3) Fetch whatever is now in the table
            stmt_select = select(users).where(users.c.username == username)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt_select).mappings().first()

            if row is None:
                logger.exception("After INSERT, no row for '%s' found", username)
                raise RuntimeError(f"Failed to retrieve user '{username}'")

            logger.debug("Returning user '%s' with id=%s", username, row["id"])
            return User(
                id=row["id"],
                username=row["username"],
                password_hash=row["password_hash"],
                is_admin=row["is_admin"],
                is_root_admin=row["is_root_admin"],
                is_temporary=row["is_temporary"],
                expires_at=row["expires_at"],
            )
        except Exception as e:
            logger.exception("Error registering user: %s", e)
            raise

    def create_temporary_user(
        self, username: str, password: str, duration_value: int = 1, duration_unit: str = "hours"
    ) -> User:
        """Creates a temporary user with expiration."""
        now = datetime.now(self.finland_tz)  # type: ignore[attr-defined]
        if duration_unit == "minutes":
            expires = now + timedelta(minutes=duration_value)
        elif duration_unit == "hours":
            expires = now + timedelta(hours=duration_value)
        elif duration_unit == "days":
            expires = now + timedelta(days=duration_value)
        else:
            expires = now + timedelta(hours=duration_value)
        expires_at = expires.isoformat()
        return self.register_user(
            username, password=password, is_temporary=True, expires_at=expires_at
        )

    def set_user_as_admin(self, username: str, is_admin: bool) -> None:
        """Sets the is_admin flag for the user with the given username."""
        self.set_user_admin_flags(username, is_admin=is_admin)

    def set_user_admin_flags(
        self,
        username: str,
        *,
        is_admin: bool,
        is_root_admin: bool | None = None,
    ) -> None:
        """Set administrative flags while preserving root status when omitted."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        values: dict[str, bool] = {"is_admin": is_admin}
        if is_root_admin is not None:
            values["is_root_admin"] = is_root_admin

        logger.debug(
            "Setting user admin flags username=%s is_admin=%s is_root_admin=%s",
            username,
            is_admin,
            is_root_admin,
        )
        try:
            stmt = update(users).where(users.c.username == username).values(**values)
            with sa_engine.begin() as conn:
                result = conn.execute(stmt)
            if result.rowcount == 0:
                raise ValueError(f"User not found: {username}")
        except Exception as e:
            logger.exception("Error setting user admin flags: %s", e)
            raise

    def authenticate_user(self, username: str, password: str) -> bool:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(users.c.password_hash).where(users.c.username == username)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return False
            return check_password_hash(row["password_hash"], password)
        except Exception as e:
            logger.exception("Error authenticating user: %s", e)
            return False

    def get_all_users(
        self,
        exclude_admin: bool = False,
        exclude_current: bool = False,
        exclude_expired: bool = False,
    ) -> list[User]:
        """Returns a list of all users with optional filtering."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(users)
            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            user_list = [
                User(
                    id=row["id"],
                    username=row["username"],
                    password_hash=row["password_hash"],
                    is_admin=row["is_admin"],
                    is_root_admin=row["is_root_admin"],
                    is_temporary=row["is_temporary"],
                    expires_at=row["expires_at"],
                )
                for row in rows
            ]

            if exclude_admin:
                user_list = [user for user in user_list if not user.is_admin]
            if exclude_current:
                user_list = [user for user in user_list if user.username != current_user.get_id()]
            if exclude_expired:
                now = datetime.now(self.finland_tz)  # type: ignore[attr-defined]
                user_list = [
                    user
                    for user in user_list
                    if not user.is_temporary
                    or not user.expires_at
                    or datetime.fromisoformat(user.expires_at) > now
                ]
            return user_list
        except Exception as e:
            logger.exception("Error fetching all users: %s", e)
            return []

    def get_user_by_username(self, username: str, include_pw: bool = True) -> User | None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(users).where(users.c.username == username)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if not row:
                return None
            return User(
                id=row["id"],
                username=row["username"],
                password_hash=row["password_hash"] if include_pw else None,
                is_admin=row["is_admin"],
                is_root_admin=row["is_root_admin"],
                is_temporary=row["is_temporary"],
                expires_at=row["expires_at"],
            )
        except Exception as e:
            logger.exception("Error fetching user by username: %s", e)
            return None

    def delete_user(self, username: str) -> None:
        """Deletes a user by username."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = delete(users).where(users.c.username == username)
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error deleting user: %s", e)
            raise

    def delete_temporary_users(self) -> None:
        """Deletes all temporary users."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = delete(users).where(users.c.is_temporary == True)  # noqa: E712
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error deleting temporary users: %s", e)
            raise

    def delete_expired_temporary_users(self) -> None:
        """Deletes all expired temporary users."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]
            stmt = delete(users).where(
                users.c.is_temporary == True,  # noqa: E712
                users.c.expires_at.isnot(None),
                users.c.expires_at < now,
            )
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error deleting expired temporary users: %s", e)
            raise

    def update_user(
        self,
        current_username: str,
        new_username: str | None = None,
        password: str | None = None,
        is_temporary: bool | None = None,
        is_admin: bool | None = None,
        expires_at: str | None = None,
    ) -> User:
        """
        Updates the given user's fields. Pass None for fields you don't want to change.
        If is_temporary is False, expires_at will be set to NULL regardless of value.
        Returns the updated User object.
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            # First, get current user
            stmt_select = select(users).where(users.c.username == current_username)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt_select).mappings().first()

            if row is None:
                raise ValueError("User not found")

            values_to_update: dict[str, Any] = {}

            if new_username and new_username != current_username:
                values_to_update["username"] = new_username

            if password:
                pw_hash = generate_password_hash(password)
                values_to_update["password_hash"] = pw_hash

            if is_admin is not None:
                values_to_update["is_admin"] = is_admin

            if is_temporary is not None:
                values_to_update["is_temporary"] = is_temporary
                if is_temporary:
                    # Set provided expires_at (can be None, meaning no expiry yet)
                    values_to_update["expires_at"] = expires_at
                else:
                    # Clear expiry when switching to permanent
                    values_to_update["expires_at"] = None
            elif expires_at is not None:
                # Only update expiry if caller asked and did not change is_temporary
                values_to_update["expires_at"] = expires_at

            if not values_to_update:
                # Nothing to change; return current user state
                return User(
                    id=row["id"],
                    username=row["username"],
                    password_hash=row["password_hash"],
                    is_admin=row["is_admin"],
                    is_root_admin=row["is_root_admin"],
                    is_temporary=row["is_temporary"],
                    expires_at=row["expires_at"],
                )

            stmt_update = (
                update(users).where(users.c.username == current_username).values(**values_to_update)
            )

            with sa_engine.begin() as conn:
                conn.execute(stmt_update)

            return self.get_user_by_username(new_username or current_username)
        except IntegrityError as ie:
            # Likely UNIQUE constraint failure on username
            raise ValueError("Käyttäjätunnus on jo käytössä.") from ie
        except Exception as e:
            logger.exception("Error updating user: %s", e)
            raise

    # --- API key management ---
    def create_api_key(self, name: str, created_by: str | None = None) -> tuple[ApiKey, str]:
        """Create a new API key. Stores only a salted hash; returns the full token once.

        Token format: 'sk_' + key_id + '_' + secret
        - key_id: 16 hex chars (64-bit randomness)
        - secret: 43+ chars URL-safe random string
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        if not name or not name.strip():
            raise ValueError("Key name is required")

        try:
            key_id = secrets.token_hex(8)  # 64-bit id, hex
            secret = secrets.token_urlsafe(32)
            token = f"sk_{key_id}_{secret}"
            # Use a password hash to store the secret (includes salt and iterations)
            secret_hash = generate_password_hash(secret)
            now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]

            stmt = (
                insert(api_keys)
                .values(
                    key_id=key_id,
                    name=name.strip(),
                    secret_hash=secret_hash,
                    created_at=now,
                    created_by=created_by,
                    revoked=False,
                    last_used_at=None,
                )
                .returning(
                    api_keys.c.id,
                    api_keys.c.key_id,
                    api_keys.c.name,
                    api_keys.c.created_at,
                    api_keys.c.created_by,
                    api_keys.c.revoked,
                    api_keys.c.last_used_at,
                )
            )

            with sa_engine.begin() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                raise RuntimeError("Failed to create API key")

            api_key = ApiKey(
                id=row["id"],
                key_id=row["key_id"],
                name=row["name"],
                created_at=row["created_at"],
                created_by=row["created_by"],
                revoked=bool(row["revoked"]),
                last_used_at=row["last_used_at"],
            )
            return api_key, token
        except Exception as e:
            logger.exception("Error creating API key: %s", e)
            raise

    def list_api_keys(self) -> list[ApiKey]:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(api_keys).order_by(api_keys.c.id.desc())
            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            return [
                ApiKey(
                    id=row["id"],
                    key_id=row["key_id"],
                    name=row["name"],
                    created_at=row["created_at"],
                    created_by=row["created_by"],
                    revoked=bool(row["revoked"]),
                    last_used_at=row["last_used_at"],
                )
                for row in rows
            ]
        except Exception as e:
            logger.exception("Error listing API keys: %s", e)
            return []

    def delete_api_key(self, key_id: str) -> None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = delete(api_keys).where(api_keys.c.key_id == key_id)
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error deleting API key: %s", e)
            raise

    def revoke_api_key(self, key_id: str) -> None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = update(api_keys).where(api_keys.c.key_id == key_id).values(revoked=True)
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error revoking API key: %s", e)
            raise

    def verify_api_key_token(self, token: str) -> dict | None:
        """Verify a presented API key token and return key metadata on success.

        On success, updates last_used_at. Returns dict with key fields; otherwise None.
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            if not token or not token.startswith("sk_"):
                return None
            rest = token[3:]
            idx = rest.find("_")
            if idx <= 0:
                return None
            key_id = rest[:idx]
            secret = rest[idx + 1 :]
            if not key_id or not secret:
                return None
        except Exception:
            return None

        try:
            stmt = select(api_keys).where(api_keys.c.key_id == key_id)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None or bool(row["revoked"]):
                return None
            if not check_password_hash(row["secret_hash"], secret):
                return None

            # Update last_used_at best-effort
            last_used = row["last_used_at"]
            try:
                now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]
                stmt_update = (
                    update(api_keys).where(api_keys.c.id == row["id"]).values(last_used_at=now)
                )
                with sa_engine.begin() as conn:
                    conn.execute(stmt_update)
                last_used = now  # Update return value with new timestamp
            except Exception:
                pass

            return {
                "id": row["id"],
                "key_id": row["key_id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "created_by": row["created_by"],
                "revoked": bool(row["revoked"]),
                "last_used_at": last_used,
            }
        except Exception as e:
            logger.exception("Error verifying API key: %s", e)
            return None
