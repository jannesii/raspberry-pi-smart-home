"""Optional Spotify account and conservative, single-interruption restoration.

Tokens are encrypted at rest with a separately configured Fernet key. Only the
HTTP boundary is replaceable; persistence, OAuth policy and ownership are real.
Restoration leases are deliberately process-local: restart loses ownership and
never resumes music. A missing/changed player state is insufficient evidence.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken

from .content import AnnouncementInputError, AnnouncementStateError

SCOPES = frozenset(("user-read-playback-state", "user-modify-playback-state"))
API = "https://api.spotify.com/v1"
logger = logging.getLogger(__name__)


class SpotifyError(Exception):
    """Credential-free error codes only; never surface HTTP response bodies."""


class SpotifyHTTP:
    def __init__(self, client_id, client_secret, redirect_uri, *, request=None):
        self.client_id, self.client_secret = client_id, client_secret
        self.redirect_uri = redirect_uri
        self.request = request or requests.request

    def token(self, **form):
        try:
            response = self.request(
                "POST",
                "https://accounts.spotify.com/api/token",
                data=form,
                auth=(self.client_id, self.client_secret),
                timeout=10,
            )
            if response.status_code != 200:
                raise SpotifyError(
                    "reconnect_required"
                    if response.status_code in (400, 401)
                    else "spotify_unavailable"
                )
            tokens = response.json()
            if (
                not isinstance(tokens, dict)
                or not isinstance(tokens.get("access_token"), str)
                or not isinstance(tokens.get("expires_in"), int)
                or tokens["expires_in"] <= 0
            ):
                raise SpotifyError("spotify_unavailable")
            return tokens
        except (requests.RequestException, ValueError) as exc:
            raise SpotifyError("spotify_unavailable") from exc

    def call(self, method, path, access_token, *, data=None, params=None):
        try:
            response = self.request(
                method,
                API + path,
                headers={"Authorization": "Bearer " + access_token},
                json=data,
                params=params,
                timeout=10,
            )
            if response.status_code in (401, 403):
                raise SpotifyError(
                    "reconnect_required" if response.status_code == 401 else "access_denied"
                )
            if response.status_code not in (200, 204):
                raise SpotifyError("spotify_unavailable")
            return response.json() if response.status_code == 200 else None
        except requests.RequestException as exc:
            raise SpotifyError(
                "request_uncertain" if method != "GET" else "spotify_unavailable"
            ) from exc
        except ValueError as exc:
            raise SpotifyError("spotify_unavailable") from exc


class SpotifyRestoration:
    def __init__(self, ctrl, *, http=None, key=None, clock=time.time, token_source=None):
        self.ctrl, self.http, self.clock = ctrl, http, clock
        try:
            self.cipher = Fernet(key.encode() if isinstance(key, str) else key) if key else None
        except (ValueError, TypeError):
            self.cipher = None
        self.reason = None
        self._settings_reason = None
        self.leases = {}
        self._state_lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._token_source = token_source
        self._generation = 0
        self._operation = None
        self._device_results = []
        self._devices_checked = False
        self._pending = []
        self._worker = None

    @property
    def configured(self):
        if self.http is None or self.cipher is None:
            return False
        parts = urlsplit(self.http.redirect_uri)
        return bool(
            self.http.client_id
            and self.http.client_secret
            and parts.scheme == "https"
            and parts.netloc
            and not parts.username
            and not parts.fragment
        )

    def status(self):
        row = self.ctrl.get_announcement_spotify()
        connected = bool(row)
        credentials_status = "missing"
        premium_status = "required_not_reported"
        reason = self._settings_reason or self.reason
        if row and self.cipher:
            try:
                tokens = json.loads(self.cipher.decrypt(row["credentials"].encode()))
                premium_status = (
                    "reported_premium"
                    if tokens.get("premium_reported")
                    else "required_not_reported"
                )
                credentials_status = "expired" if tokens["expires_at"] <= self.clock() else "valid"
            except (InvalidToken, ValueError, TypeError, KeyError):
                reason, credentials_status = "reconnect_required", "unreadable"
        return {
            "operation": self._operation,
            "available_devices": list(self._device_results),
            "devices_checked": self._devices_checked,
            "available": self.configured and connected and reason is None,
            "credentials_status": credentials_status,
            "premium_status": premium_status,
            "configured": self.configured,
            "connected": connected,
            "enabled": bool(row and row["enabled"]),
            "devices": json.loads(row["devices"]) if row else {},
            "reason": (
                "not_configured"
                if not self.configured
                else reason or (None if connected else "disconnected")
            ),
            "prerequisites": "Spotify Premium and approved playback-control access are required.",
        }

    def _queue(self, kind, *args):
        # Only the latest settings intent is relevant. Provider waits happen in
        # this feature-local daemon, independent of requests and speech ticks.
        with self._state_lock:
            if kind != "available_devices":
                self._generation += 1
                self._pending.clear()
            elif any(job[1] == kind for job in self._pending):
                return self.status()
            self._pending.append((self._generation, kind, args))
            self._operation = {
                "connect": "connecting",
                "configure": "saving",
                "available_devices": "loading_devices",
            }[kind]
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._settings_worker, name="AnnouncementSpotify", daemon=True
                )
                self._worker.start()
            return self.status()

    def request_connect(self, code):
        with self._state_lock:
            # A reconnect is newer owner intent: stop optional control of the
            # previous account immediately while the exchange is pending.
            self.configure(False, None, ())
            self.leases.clear()
            return self._queue("connect", code)

    def request_devices(self):
        return self._queue("available_devices")

    def request_configure(self, enabled, devices, targets):
        self._validate_configuration(enabled, devices, targets)
        if not enabled:
            with self._state_lock:
                self._generation += 1
                self._pending = []
                self._operation = None
                self._settings_reason = None
                return self.configure(enabled, devices, targets)
        if not self.configured or not self.ctrl.get_announcement_spotify():
            raise AnnouncementStateError(
                "unavailable", "not_configured" if not self.configured else "disconnected"
            )
        return self._queue("configure", enabled, devices, targets)

    def _settings_worker(self):
        while True:
            with self._state_lock:
                if not self._pending:
                    self._worker = None
                    return
                generation, kind, args = self._pending.pop(0)
                staged = _SpotifySettings(self.ctrl.get_announcement_spotify())
            isolated = SpotifyRestoration(
                staged, http=self.http, clock=self.clock, token_source=self
            )
            isolated.cipher = self.cipher
            error = None
            result = None
            try:
                result = getattr(isolated, kind)(*args)
            except (SpotifyError, AnnouncementStateError) as exc:
                error = str(exc)
            except Exception:
                # Provider implementations must never leak their response or
                # credentials through logs or status on unexpected failures.
                error = "spotify_unavailable"
            with self._state_lock:
                if generation != self._generation:
                    continue
                if error is None:
                    if staged.changed:
                        values = dict(staged.row)
                        if kind != "connect":
                            current = self.ctrl.get_announcement_spotify()
                            if current is None:
                                self.reason = "disconnected"
                                self._operation = None
                                continue
                            # Playback may refresh the same account while a
                            # device/settings HTTP read waits. Publish settings
                            # without rolling back its newer rotated credentials.
                            if current["credentials"] != staged.source_credentials:
                                values["credentials"] = current["credentials"]
                        self.ctrl.save_announcement_spotify(**values)
                    if kind == "available_devices":
                        self._device_results = result
                        self._devices_checked = True
                    if kind == "connect":
                        self.leases.clear()
                        logger.info(
                            "Optional Spotify account connected; restoration disabled until enabled"
                        )
                elif self.reason != error:
                    logger.warning(
                        "Optional Spotify settings unavailable operation=%s reason=%s", kind, error
                    )
                self._settings_reason = error
                self.reason = error
                if not self._pending:
                    self._operation = None

    def authorization(self):
        if not self.configured:
            raise AnnouncementStateError(
                "unavailable", "Spotify is not configured; basic announcements still work."
            )
        state = secrets.token_urlsafe(32)
        url = "https://accounts.spotify.com/authorize?" + urlencode(
            {
                "client_id": self.http.client_id,
                "response_type": "code",
                "redirect_uri": self.http.redirect_uri,
                "state": state,
                "scope": " ".join(sorted(SCOPES)),
            }
        )
        return state, url

    def _save(self, tokens, *, enabled=None, devices=None):
        row = self.ctrl.get_announcement_spotify()
        self.ctrl.save_announcement_spotify(
            credentials=self.cipher.encrypt(json.dumps(tokens).encode()).decode(),
            enabled=bool(row and row["enabled"]) if enabled is None else enabled,
            devices=(row["devices"] if row else "{}") if devices is None else json.dumps(devices),
        )

    def _tokens(self):
        if self._token_source is not None:
            return self._token_source._tokens()
        if not self.configured:
            raise SpotifyError("not_configured")
        row = self.ctrl.get_announcement_spotify()
        if row is None:
            raise SpotifyError("disconnected")
        try:
            tokens = json.loads(self.cipher.decrypt(row["credentials"].encode()))
        except (InvalidToken, ValueError, TypeError):
            raise SpotifyError("reconnect_required") from None
        if tokens["expires_at"] > self.clock() + 30:
            return tokens
        # One account owns refresh, including settings-worker reads. Do not
        # block speech on optional provider refresh; unknown is safer than
        # issuing concurrent rotating refresh tokens or accepting idle.
        if not self._refresh_lock.acquire(blocking=False):
            raise SpotifyError("refresh_pending")
        try:
            current = self.ctrl.get_announcement_spotify()
            if current is None:
                raise SpotifyError("disconnected")
            if current["credentials"] != row["credentials"]:
                return self._tokens()
            refreshed = self.http.token(
                grant_type="refresh_token", refresh_token=tokens["refresh_token"]
            )
            tokens.update(
                access_token=refreshed["access_token"],
                refresh_token=refreshed.get("refresh_token", tokens["refresh_token"]),
                expires_at=self.clock() + refreshed["expires_in"],
            )
            with self._state_lock:
                # A late refresh cannot resurrect a disconnected/replaced account.
                current = self.ctrl.get_announcement_spotify()
                if current is None or current["credentials"] != row["credentials"]:
                    raise SpotifyError("newer_intent")
                self._save(tokens)
            return tokens
        finally:
            self._refresh_lock.release()

    def _call(self, method, path, **kwargs):
        generation = self._generation
        try:
            token = self._tokens()["access_token"]
            with self._state_lock:
                if generation != self._generation:
                    raise SpotifyError("newer_intent")
            result = self.http.call(method, path, token, **kwargs)
            with self._state_lock:
                if generation != self._generation:
                    raise SpotifyError("newer_intent")
                self.reason = None
            return result
        except SpotifyError as exc:
            if generation != self._generation:
                raise
            if self.reason != str(exc):
                logger.warning("Optional Spotify access unavailable reason=%s", str(exc))
            self.reason = str(exc)
            raise

    def connect(self, code):
        if not self.configured:
            raise SpotifyError("not_configured")
        tokens = self.http.token(
            grant_type="authorization_code", code=code, redirect_uri=self.http.redirect_uri
        )
        if not set(tokens.get("scope", "").split()) >= SCOPES or not tokens.get("refresh_token"):
            raise SpotifyError("scopes_required")
        profile = self.http.call("GET", "/me", tokens["access_token"])
        if not profile or profile.get("product") not in (None, "premium"):
            raise SpotifyError("premium_required")
        tokens["premium_reported"] = profile.get("product") == "premium"
        tokens["expires_at"] = self.clock() + tokens["expires_in"]
        self._save(tokens, enabled=False, devices={})
        self.reason = None
        self.leases.clear()
        return self.status()

    def _validate_configuration(self, enabled, devices, targets):
        if not isinstance(enabled, bool):
            raise AnnouncementInputError("enabled", "Choose whether to restore interrupted music.")
        if devices is not None and (
            not isinstance(devices, dict)
            or any(
                not isinstance(k, str)
                or not isinstance(v, str)
                or not v
                or len(v) > 128
                or not any(t.key == k for t in targets)
                for k, v in devices.items()
            )
        ):
            raise AnnouncementInputError(
                "devices", "Map configured speaker targets to Spotify device ids."
            )

    def configure(self, enabled, devices, targets):
        self._validate_configuration(enabled, devices, targets)
        if not enabled:
            # Turning off optional control must work even with revoked tokens
            # or a lost encryption key. Preserve ciphertext; make no HTTP call.
            row = self.ctrl.get_announcement_spotify()
            if row:
                self.ctrl.save_announcement_spotify(
                    credentials=row["credentials"],
                    enabled=False,
                    devices=row["devices"] if devices is None else json.dumps(devices),
                )
            return self.status()
        try:
            tokens = self._tokens()
            self._save(tokens, enabled=enabled, devices=devices)
        except SpotifyError as exc:
            raise AnnouncementStateError("unavailable", str(exc)) from None
        self.reason = None
        return self.status()

    def available_devices(self):
        response = self._call("GET", "/me/player/devices") or {}
        return [
            {
                "id": d["id"],
                "name": d.get("name") or "Spotify device",
                "restricted": bool(d.get("is_restricted")),
            }
            for d in response.get("devices", [])
            if d.get("id")
        ]

    def disconnect(self):
        with self._state_lock:
            self._generation += 1
            self._pending = []
            self._operation = None
            self._device_results = []
            self._devices_checked = False
            self.ctrl.delete_announcement_spotify()
            self.reason = None
            self._settings_reason = None
        logger.info("Optional Spotify account disconnected")
        return self.status()

    @staticmethod
    def _identity(state):
        if not isinstance(state, dict):
            return None
        device = state.get("device") or {}
        item = state.get("item") or {}
        return (
            device.get("id"),
            item.get("uri"),
            (state.get("context") or {}).get("uri"),
            state.get("progress_ms"),
            state.get("timestamp"),
            state.get("is_playing"),
        )

    def media_state(self, target):
        status = self.status()
        device_id = status["devices"].get(target.key)
        if not status["enabled"] or not status["configured"] or not device_id:
            return None
        try:
            player = self._call("GET", "/me/player")
        except SpotifyError:
            return "unknown"
        if player and (player.get("device") or {}).get("id") == device_id:
            return "playing" if player.get("is_playing") else "idle"
        return None

    def capture(self, attempt_id, target):
        status = self.status()
        if not status["enabled"] or not status["available"]:
            return "disabled" if status["connected"] and not status["enabled"] else "unavailable"
        device_id = status["devices"].get(target.key)
        if not device_id:
            return "unmapped_target"
        try:
            before = self._call("GET", "/me/player")
            if (
                not before
                or before.get("is_playing") is not True
                or (before.get("device") or {}).get("id") != device_id
            ):
                return "skipped_newer_intent"
            if not (before.get("item") or {}).get("uri") or before.get("progress_ms") is None:
                return "unknown"
            # Explicitly pause before Cast changes the receiver. Keep the exact
            # confirmed paused state as the only acceptable restoration guard.
            self._call("PUT", "/me/player/pause", params={"device_id": device_id})
            paused = self._call("GET", "/me/player")
            expected = self._identity(paused)
            if (
                not expected
                or not isinstance(expected[3], int)
                or not isinstance(expected[4], int)
                or expected[0:3] != self._identity(before)[0:3]
                or paused.get("is_playing") is not False
            ):
                return "unknown"
            before["progress_ms"] = paused["progress_ms"]
            self.leases[attempt_id] = (before, expected)
            return "pending"
        except SpotifyError:
            return "unavailable"

    def owns(self, attempt_id, applied, target):
        lease = self.leases.get(attempt_id)
        if lease is None:
            return True  # no Spotify effect to own
        before, expected = lease
        status = self.status()
        if not status["enabled"] or status["devices"].get(target.key) != expected[0]:
            return False
        try:
            current = self._call("GET", "/me/player")
        except SpotifyError:
            return False
        allowed_volume = (before["device"].get("volume_percent"), round(applied * 100))
        return bool(
            self._identity(current) == expected
            and (current.get("device") or {}).get("volume_percent") in allowed_volume
        )

    def restore(self, attempt_id, target, *, owned):
        lease = self.leases.pop(attempt_id, None)
        if lease is None:
            return None
        if not self.status()["enabled"]:
            return "disabled"
        if not owned:
            return "skipped_newer_intent"
        before, expected = lease
        try:
            current = self._call("GET", "/me/player")
            if self._identity(current) != expected:
                return "skipped_newer_intent" if current else "unknown"
            body = {"position_ms": before["progress_ms"]}
            context = (before.get("context") or {}).get("uri")
            if context:
                body.update(context_uri=context, offset={"uri": before["item"]["uri"]})
            else:
                body["uris"] = [before["item"]["uri"]]
            self._call("PUT", "/me/player/play", params={"device_id": expected[0]}, data=body)
            # A 204 acknowledges a control request, not that playback resumed.
            # Never repeat an uncertain resume; observe once and expose unknown.
            resumed = self._call("GET", "/me/player")
            if not resumed:
                return "unknown"
            if (resumed.get("device") or {}).get("id") != expected[0] or (
                resumed.get("item") or {}
            ).get("uri") != expected[1]:
                return "skipped_newer_intent"
            return "restored" if resumed.get("is_playing") is True else "unknown"
        except SpotifyError as exc:
            return (
                "unknown" if str(exc) in ("request_uncertain", "spotify_unavailable") else "failed"
            )


class _SpotifySettings:
    """Private staging for one provider operation; publish only current intent."""

    def __init__(self, row):
        self.row = dict(row) if row else None
        self.source_credentials = row["credentials"] if row else None
        self.changed = False

    def get_announcement_spotify(self):
        return self.row

    def save_announcement_spotify(self, **values):
        self.row = values
        self.changed = True
