"""Cast transport boundary and its lazily imported PyChromecast adapter.

The service only speaks :class:`CastTransport`. Observations report what the
receiver said, never that a person heard anything. Group targets use the same
interface: the group receiver (found at its leader host by its Cast UUID) is
played as one synchronized stream, and it reports which member UUIDs it
currently contains; the service turns that into group-scoped evidence.

The PyChromecast adapter is not exercised by automated tests (the library is
not installed here and tests never contact speakers); its behaviour under
Eventlet and with JBL receivers must be validated during owner commissioning.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from .config import SpeakerTarget

logger = logging.getLogger(__name__)

# Default Media Receiver: when it is the running app and idle, nothing is playing.
DEFAULT_MEDIA_RECEIVER = "CC1AD845"


class TransportError(Exception):
    """A Cast effect failed.

    ``certain_not_played`` is True only when the request provably never reached
    the receiver (e.g. connection refused); otherwise the outcome is unknown.
    """

    def __init__(self, reason: str, *, certain_not_played: bool):
        super().__init__(reason)
        self.reason = reason
        self.certain_not_played = certain_not_played


@dataclass(frozen=True)
class SpeakerProbe:
    state: str  # idle | playing | unknown | unavailable
    volume: float | None
    device_id: str | None
    # Group receivers only: Cast UUIDs the group reports as its current
    # members, or None when the receiver reported no membership.
    member_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class PlaybackObservation:
    # loading | playing | finished | failed | cancelled | interrupted | lost
    state: str
    detail: str | None = None
    # Group receivers only: membership reported during playback (or None).
    member_ids: tuple[str, ...] | None = None


class CastTransport(Protocol):
    engine: str

    def status(self) -> dict: ...

    def probe(self, target: SpeakerTarget) -> SpeakerProbe: ...

    def set_volume(self, target: SpeakerTarget, level: float) -> None: ...

    def play(
        self, attempt_id: str, target: SpeakerTarget, media_url: str, *, content_type, title
    ) -> None: ...

    def discover(self, hosts: list[str]) -> list[dict]:
        """Read-only: receivers (speakers and groups) found at ``hosts``.

        Each item: device_id, name, model, cast_type ('audio'|'group'|...),
        host, port. Never creates or changes a group.
        """
        ...

    def poll(self, attempt_id: str, target: SpeakerTarget) -> PlaybackObservation: ...

    def stop(self, attempt_id: str, target: SpeakerTarget) -> str:
        """Return ``stopped``, ``failed`` or ``unknown`` as observed."""
        ...

    def release(self, attempt_id: str) -> None: ...


class _MediaListener:
    def __init__(self, media_url: str):
        self.media_url = media_url
        self.state = "loading"
        self.detail: str | None = None
        self.started = False

    def new_media_status(self, status) -> None:
        if getattr(status, "content_id", None) not in (None, self.media_url):
            if self.started:
                self.state = "interrupted"
            return
        player = getattr(status, "player_state", None)
        if player in ("PLAYING", "BUFFERING"):
            self.started = True
            self.state = "playing"
        elif player == "IDLE":
            reason = getattr(status, "idle_reason", None)
            self.detail = reason
            self.state = {
                "FINISHED": "finished",
                "CANCELLED": "cancelled",
                "INTERRUPTED": "interrupted",
                "ERROR": "failed",
            }.get(reason, self.state)

    def load_media_failed(self, queue_item_id, error_code) -> None:
        self.state = "failed"
        self.detail = f"load_failed:{error_code}"


class _ConnectionListener:
    def __init__(self, listener: _MediaListener):
        self.listener = listener

    def new_connection_status(self, status) -> None:
        lost = getattr(status, "status", None) in ("LOST", "FAILED", "DISCONNECTED")
        if lost and self.listener.state in ("loading", "playing"):
            self.listener.state = "lost"


class PyChromecastTransport:
    """Known-host PyChromecast adapter (lazy import; no mDNS dependency)."""

    engine = "pychromecast"

    def __init__(
        self,
        *,
        connect_timeout_s: float = 10.0,
        command_timeout_s: float = 10.0,
        membership_timeout_s: float = 2.0,
    ):
        self.connect_timeout_s = connect_timeout_s
        self.command_timeout_s = command_timeout_s
        # How long a probe waits for a group's membership report (else unknown).
        self.membership_timeout_s = membership_timeout_s
        self._sessions: dict[str, tuple] = {}
        self._lock = threading.Lock()

    def _module(self):
        try:
            return importlib.import_module("pychromecast")
        except ImportError as exc:
            raise TransportError("pychromecast_not_installed", certain_not_played=True) from exc

    def status(self) -> dict:
        try:
            self._module()
        except TransportError as exc:
            return {"available": False, "engine": self.engine, "reason": exc.reason}
        return {"available": True, "engine": self.engine, "reason": None}

    @staticmethod
    def _matches(cast, target: SpeakerTarget) -> bool:
        info = cast.cast_info
        is_group = getattr(info, "cast_type", None) == "group"
        if target.is_group:
            # Only the group's own receiver: the leader speaker shares its host.
            return is_group and str(cast.uuid) == target.device_id
        if target.device_id:
            return str(cast.uuid) == target.device_id
        return not is_group and str(info.host) == target.host and info.port == target.port

    def _connect(self, target: SpeakerTarget):
        pychromecast = self._module()
        if not target.host:
            raise TransportError("target_host_not_configured", certain_not_played=True)
        if target.is_group and not target.device_id:
            raise TransportError("group_not_configured", certain_not_played=True)
        lookup = {"known_hosts": [target.host], "discovery_timeout": self.connect_timeout_s}
        if target.device_id:
            try:
                lookup["uuids"] = [UUID(target.device_id)]
            except ValueError as exc:
                raise TransportError("device_id_invalid", certain_not_played=True) from exc
        try:
            casts, browser = pychromecast.get_listed_chromecasts(**lookup)
            pychromecast.discovery.stop_discovery(browser)
        except Exception as exc:
            logger.warning("Cast discovery failed for %s: %s", target.key, type(exc).__name__)
            raise TransportError("speaker_unavailable", certain_not_played=True) from exc
        cast = next((c for c in casts if self._matches(c, target)), None)
        if cast is None:
            raise TransportError("speaker_unavailable", certain_not_played=True)
        try:
            cast.wait(timeout=self.connect_timeout_s)
        except Exception as exc:
            raise TransportError("speaker_unavailable", certain_not_played=True) from exc
        return cast

    def _multizone(self, cast):
        """Register a membership listener on a group receiver (None if unsupported)."""
        try:
            multizone = importlib.import_module("pychromecast.controllers.multizone")
            controller = multizone.MultizoneController(cast.uuid)
            cast.register_handler(controller)
            controller.update_members()
            return controller
        except Exception as exc:
            logger.info("Cast group membership unavailable: %s", type(exc).__name__)
            return None

    def _member_ids(self, controller, *, wait: bool) -> tuple[str, ...] | None:
        """Member UUIDs the group reported; None when it reported none (unknown)."""
        if controller is None:
            return None
        deadline = time.monotonic() + (self.membership_timeout_s if wait else 0)
        members = list(controller.members)
        while not members and time.monotonic() < deadline:
            time.sleep(0.1)
            members = list(controller.members)
        return tuple(str(m) for m in members) or None

    def discover(self, hosts) -> list[dict]:
        pychromecast = self._module()
        try:
            casts, browser = pychromecast.get_chromecasts(known_hosts=list(hosts))
            pychromecast.discovery.stop_discovery(browser)
        except Exception as exc:
            logger.warning("Cast discovery failed: %s", type(exc).__name__)
            raise TransportError("discovery_failed", certain_not_played=True) from exc
        found = []
        for cast in casts:  # CastInfo only: no receiver is connected or changed
            info = cast.cast_info
            found.append(
                {
                    "device_id": str(cast.uuid),
                    "name": info.friendly_name,
                    "model": info.model_name,
                    "cast_type": info.cast_type,
                    "host": str(info.host),
                    "port": info.port,
                }
            )
        return found

    def probe(self, target: SpeakerTarget) -> SpeakerProbe:
        try:
            cast = self._connect(target)
        except TransportError:
            return SpeakerProbe(state="unavailable", volume=None, device_id=None)
        try:
            status = cast.status
            media = cast.media_controller.status
            player = getattr(media, "player_state", None)
            if cast.is_idle or (status.app_id == DEFAULT_MEDIA_RECEIVER and player == "IDLE"):
                state = "idle"
            elif player in ("PLAYING", "BUFFERING"):
                state = "playing"
            else:
                state = "unknown"  # another app (e.g. Spotify) or paused: never optimistic
            member_ids = None
            if target.is_group:
                member_ids = self._member_ids(self._multizone(cast), wait=True)
            return SpeakerProbe(
                state=state,
                volume=getattr(status, "volume_level", None),
                device_id=str(cast.uuid),
                member_ids=member_ids,
            )
        finally:
            cast.disconnect(timeout=self.command_timeout_s)

    def set_volume(self, target: SpeakerTarget, level: float) -> None:
        cast = self._connect(target)
        try:
            cast.set_volume(max(0.0, min(1.0, level)), timeout=self.command_timeout_s)
        except Exception as exc:
            raise TransportError("volume_failed", certain_not_played=True) from exc
        finally:
            cast.disconnect(timeout=self.command_timeout_s)

    def play(self, attempt_id, target, media_url, *, content_type, title) -> None:
        cast = self._connect(target)
        listener = _MediaListener(media_url)
        try:
            cast.media_controller.register_status_listener(listener)
            cast.register_connection_listener(_ConnectionListener(listener))
            multizone = self._multizone(cast) if target.is_group else None
            with self._lock:
                self._sessions[attempt_id] = (cast, listener, multizone)
            cast.media_controller.play_media(
                media_url, content_type, title=title, stream_type="BUFFERED"
            )
        except Exception as exc:
            # The load may have reached the receiver before the failure.
            raise TransportError("load_uncertain", certain_not_played=False) from exc

    def poll(self, attempt_id, target) -> PlaybackObservation:
        with self._lock:
            session = self._sessions.get(attempt_id)
        if session is None:
            return PlaybackObservation(state="lost", detail="no_session")
        _cast, listener, multizone = session
        return PlaybackObservation(
            state=listener.state,
            detail=listener.detail,
            member_ids=self._member_ids(multizone, wait=False),
        )

    def stop(self, attempt_id, target) -> str:
        with self._lock:
            session = self._sessions.get(attempt_id)
        if session is None:
            return "unknown"
        cast, listener, _multizone = session
        try:
            cast.media_controller.stop(timeout=self.command_timeout_s)
        except Exception as exc:
            logger.warning("Cast stop failed for %s: %s", target.key, type(exc).__name__)
            return "unknown"
        return "stopped" if listener.state in ("cancelled", "finished") else "unknown"

    def release(self, attempt_id) -> None:
        with self._lock:
            session = self._sessions.pop(attempt_id, None)
        if session:
            try:
                session[0].disconnect(timeout=self.command_timeout_s)
            except Exception:
                logger.debug("Cast disconnect failed during release", exc_info=True)
