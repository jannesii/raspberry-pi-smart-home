import logging
import secrets
import sqlite3
from datetime import datetime, timedelta

from flask_login import current_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..models import ApiKey, User

logger = logging.getLogger(__name__)


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
        Uses INSERT OR IGNORE to avoid UNIQUE errors, then SELECT to fetch.
        """
        logger.debug(f"Registering user: {username}")

        # 1) Hash the password up front
        if password_hash is None and password:
            pw_hash = generate_password_hash(password)
        elif password is None and password_hash is None:
            raise ValueError("Either password or password_hash must be provided")
        else:
            pw_hash = password_hash

        # 2) Try to insert; if username exists, this is a no-op
        cursor = self.db.execute_query(
            "INSERT OR IGNORE INTO users (username, password_hash, is_admin, is_root_admin, is_temporary, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                username,
                pw_hash,
                1 if is_admin else 0,
                1 if is_root_admin else 0,
                1 if is_temporary else 0,
                expires_at,
            ),
        )
        if cursor.rowcount == 1:
            logger.debug(f"User '{username}' created successfully.")
        else:
            logger.debug(f"User '{username}' already exists, skipping INSERT.")

        # 3) Fetch whatever is now in the table
        row = self.db.fetchone(
            "SELECT id, username, password_hash, is_admin, is_root_admin, is_temporary, expires_at FROM users WHERE username = ?",
            (username,),
        )
        if row is None:
            # This really should never happen
            logger.exception(f"After INSERT OR IGNORE, no row for '{username}' found.")
            raise RuntimeError(f"Failed to retrieve user '{username}'")

        logger.debug(f"Returning user '{username}' with id={row['id']}")
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            is_admin=row["is_admin"],
            is_root_admin=row["is_root_admin"],
            is_temporary=row["is_temporary"],
            expires_at=row["expires_at"],
        )

    def create_temporary_user(
        self, username: str, password: str, duration_value: int = 1, duration_unit: str = "hours"
    ) -> User:
        """Creates a temporary user with expiration."""
        now = datetime.now(self.finland_tz)
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
        self.db.execute_query(
            "UPDATE users SET is_admin = ? WHERE username = ?", (is_admin, username)
        )

    def authenticate_user(self, username: str, password: str) -> bool:
        row = self.db.fetchone("SELECT password_hash FROM users WHERE username = ?", (username,))
        if row is None:
            return False
        return check_password_hash(row["password_hash"], password)

    def get_all_users(
        self,
        exclude_admin: bool = False,
        exclude_current: bool = False,
        exclude_expired: bool = False,
    ) -> list[User]:
        """Palauttaa listan kaikista käyttäjistä."""
        rows = self.db.fetchall(
            "SELECT id, username, password_hash, is_admin, is_root_admin, is_temporary, expires_at FROM users",
            (),
        )
        users = [
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
            users = [user for user in users if not user.is_admin]
        if exclude_current:
            users = [user for user in users if user.username != current_user.get_id()]
        if exclude_expired:
            now = datetime.now(self.finland_tz)
            users = [
                user
                for user in users
                if not user.is_temporary
                or not user.expires_at
                or datetime.fromisoformat(user.expires_at) > now
            ]
        return users

    def get_user_by_username(self, username: str, include_pw: bool = True) -> User | None:
        row = self.db.fetchone(
            "SELECT id, username, password_hash, is_admin, is_root_admin, is_temporary, expires_at FROM users WHERE username = ?",
            (username,),
        )
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

    def delete_user(self, username: str) -> None:
        """Poistaa käyttäjän annetulla käyttäjätunnuksella."""
        self.db.execute_query("DELETE FROM users WHERE username = ?", (username,))

    def delete_temporary_users(self) -> None:
        """Deletes all temporary users."""
        self.db.execute_query("DELETE FROM users WHERE is_temporary = 1", ())

    def delete_expired_temporary_users(self) -> None:
        """Deletes all expired temporary users."""
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "DELETE FROM users WHERE is_temporary = 1 AND expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )

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
        row = self.db.fetchone(
            "SELECT id, username, password_hash, is_admin, is_temporary, expires_at FROM users WHERE username = ?",
            (current_username,),
        )
        if row is None:
            raise ValueError("User not found")

        updates: list[str] = []
        params: list[object] = []

        if new_username and new_username != current_username:
            updates.append("username = ?")
            params.append(new_username)

        if password:
            pw_hash = generate_password_hash(password)
            updates.append("password_hash = ?")
            params.append(pw_hash)

        if is_admin is not None:
            updates.append("is_admin = ?")
            params.append(1 if is_admin else 0)

        if is_temporary is not None:
            updates.append("is_temporary = ?")
            params.append(1 if is_temporary else 0)
            if is_temporary:
                # Set provided expires_at (can be None, meaning no expiry yet)
                updates.append("expires_at = ?")
                params.append(expires_at)
            else:
                # Clear expiry when switching to permanent
                updates.append("expires_at = NULL")

        elif expires_at is not None:
            # Only update expiry if caller asked and did not change is_temporary
            updates.append("expires_at = ?")
            params.append(expires_at)

        if not updates:
            # Nothing to change; return current user state
            return User(
                id=row["id"],
                username=row["username"],
                password_hash=row["password_hash"],
                is_admin=row["is_admin"],
                is_temporary=row["is_temporary"],
                expires_at=row["expires_at"],
            )

        query = f"UPDATE users SET {', '.join(updates)} WHERE username = ?"
        params.append(current_username)

        try:
            self.db.execute_query(query, tuple(params))
        except sqlite3.IntegrityError as ie:
            # Likely UNIQUE constraint failure on username
            raise ValueError("Käyttäjätunnus on jo käytössä.") from ie

        return self.get_user_by_username(new_username or current_username)

    # --- API key management ---
    def create_api_key(self, name: str, created_by: str | None = None) -> tuple[ApiKey, str]:
        """Create a new API key. Stores only a salted hash; returns the full token once.

        Token format: 'sk_' + key_id + '_' + secret
        - key_id: 16 hex chars (64-bit randomness)
        - secret: 43+ chars URL-safe random string
        """
        if not name or not name.strip():
            raise ValueError("Key name is required")
        key_id = secrets.token_hex(8)  # 64-bit id, hex
        secret = secrets.token_urlsafe(32)
        token = f"sk_{key_id}_{secret}"
        # Use a password hash to store the secret (includes salt and iterations)
        secret_hash = generate_password_hash(secret)
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO api_keys (key_id, name, secret_hash, created_at, created_by, revoked, last_used_at) VALUES (?, ?, ?, ?, ?, 0, NULL)",
            (key_id, name.strip(), secret_hash, now, created_by),
        )
        row = self.db.fetchone(
            "SELECT id, key_id, name, created_at, created_by, revoked, last_used_at FROM api_keys WHERE key_id = ?",
            (key_id,),
        )
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

    def list_api_keys(self) -> list[ApiKey]:
        rows = self.db.fetchall(
            "SELECT id, key_id, name, created_at, created_by, revoked, last_used_at FROM api_keys ORDER BY id DESC",
            (),
        )
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

    def delete_api_key(self, key_id: str) -> None:
        self.db.execute_query("DELETE FROM api_keys WHERE key_id = ?", (key_id,))

    def revoke_api_key(self, key_id: str) -> None:
        self.db.execute_query("UPDATE api_keys SET revoked = 1 WHERE key_id = ?", (key_id,))

    def verify_api_key_token(self, token: str) -> dict | None:
        """Verify a presented API key token and return key metadata on success.

        On success, updates last_used_at. Returns dict with key fields; otherwise None.
        """
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

        row = self.db.fetchone(
            "SELECT id, key_id, name, secret_hash, created_at, created_by, revoked, last_used_at FROM api_keys WHERE key_id = ?",
            (key_id,),
        )
        if row is None or bool(row["revoked"]):
            return None
        if not check_password_hash(row["secret_hash"], secret):
            return None
        # Update last_used_at best-effort
        try:
            now = datetime.now(self.finland_tz).isoformat()
            self.db.execute_query(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now, row["id"])
            )
        except Exception:
            pass
        return {
            "id": row["id"],
            "key_id": row["key_id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "created_by": row["created_by"],
            "revoked": bool(row["revoked"]),
            "last_used_at": row["last_used_at"],
        }
