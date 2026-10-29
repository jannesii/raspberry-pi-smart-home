"""App-owned announcement service: previews, speaker tests and delivery attempts.

``tick()`` is the public processing interface: the owned loop calls it, tests
call it directly with a controlled clock. Web/API handlers only validate input
and record requests; synthesis and Cast waits happen in ``tick()``.

Content preparation (``content``), media preparation (``media``) and delivery
(``transport``) stay separate so later work can add schedules/occurrences
(#32), fallback (#33) or a Cast group (#37) by creating attempts and reacting
to their outcomes rather than re-implementing dispatch.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
import wave
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from .access import MediaGrants, media_path
from .config import (
    DEFAULT_CHIME_PERCENT,
    DEFAULT_LANGUAGE,
    DEFAULT_VOLUME_PERCENT,
    LANGUAGES,
    AnnouncementConfig,
    SpeakerTarget,
)
from .content import (
    AnnouncementInputError,
    AnnouncementStateError,
    ContentProvider,
    PreparedContent,
    StaticTextProvider,
    parse_percent,
)
from .fallback import DiscordFallbackChannel, FallbackMixin
from .groups import CastDiscoveryMixin, combine, observe_playback
from .media import MediaCache, MediaError, PreparedMedia, compose, media_key
from .occurrences import OccurrenceMixin
from .schedules import DEFAULT_EXPIRY_MINUTES, QuietHours
from .spotify import SpotifyRestoration
from .synthesis import SynthesisError, Synthesizer
from .transport import CastTransport, TransportError

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = ["AnnouncementService", "AnnouncementStateError"]

ACTIVE = ("dispatching", "dispatched", "playing")
TERMINAL = ("finished", "failed", "unknown", "stopped")
PREVIEW_TTL_S = 1800
PREVIEW_LIMIT = 20
EVICTION_INTERVAL_S = 600
VOLUME_EPSILON = 0.005


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def _wav_duration(path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / wav.getframerate()
    except (OSError, wave.Error, EOFError):
        return None


class AnnouncementService(OccurrenceMixin, FallbackMixin, CastDiscoveryMixin):
    def __init__(
        self,
        ctrl,
        *,
        synthesizer: Synthesizer,
        transport: CastTransport,
        config: AnnouncementConfig,
        clock: Callable[[], float] = time.time,
        content_provider: ContentProvider | None = None,
        tick_interval_s: float = 1.0,
        fallback_channel: DiscordFallbackChannel | None = None,
        spotify: SpotifyRestoration | None = None,
    ):
        self.ctrl = ctrl
        self.spotify = spotify or SpotifyRestoration(ctrl, clock=clock)
        self.synthesizer = synthesizer
        self.transport = transport
        self.config = config
        self._clock = clock
        self.content = content_provider or StaticTextProvider(config.max_text_length)
        # Unconfigured by default: only production wiring passes the webhook.
        self.fallback_channel = fallback_channel or DiscordFallbackChannel(None)
        self.cache = MediaCache(
            config.cache_dir,
            clock=clock,
            max_age_s=config.cache_max_age_s,
            max_bytes=config.cache_max_bytes,
        )
        self.grants = MediaGrants(clock)
        self.tick_interval_s = tick_interval_s
        self._previews: dict[str, dict] = {}
        self._last_probe: dict[str, dict] = {}
        self._last_eviction: float | None = None
        self.quiet_hours = QuietHours.parse(config.quiet_hours)
        # Last time each target was seen playing or in an unknown media state:
        # ordinary speech waits until it has been idle for the debounce window
        # so a track change is not mistaken for music ending.
        self._media_busy_seen: dict[str, float] = {}
        self._next_probe: dict[str, float] = {}
        self._discovery: dict | None = None
        self._lock = threading.RLock()
        self._processing_lock = threading.Lock()
        self._finishing = set()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.recover()

    # Lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="Announcements", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Announcement tick failed")
            self._wake.wait(self.tick_interval_s)
            self._wake.clear()

    def recover(self) -> int:
        """Mark attempts interrupted between intent and outcome as unknown.

        They are never replayed: a missed test is preferable to an unintended
        duplicate. Volume is left alone because ownership is uncertain.
        """
        now = _iso(self._clock())
        recovered = 0
        for attempt in self.ctrl.list_announcement_attempts(statuses=ACTIVE):
            values = {
                "status": "unknown",
                "reason": "interrupted_by_restart",
                "finished_at": now,
                "volume_restore": "unknown" if attempt.volume_applied is not None else None,
                "media_restore": "unknown"
                if attempt.media_restore == "pending"
                else attempt.media_restore,
                "updated_at": now,
            }
            if attempt.stop_requested_at and attempt.stop_outcome in (None, "failed", "unknown"):
                values["stop_outcome"] = "unknown"
            if self.ctrl.update_announcement_attempt(attempt.id, expected=ACTIVE, **values):
                recovered += 1
        # Playback can be committed before a process dies restoring volume/music.
        # Preserve its terminal delivery evidence; ownership cannot survive restart.
        terminal = ("finished", "failed", "unknown", "stopped")
        for attempt in self.ctrl.list_announcement_attempts(statuses=terminal):
            values = {}
            if attempt.volume_applied is not None and attempt.volume_restore is None:
                values["volume_restore"] = "unknown"
            if attempt.media_restore == "pending":
                values["media_restore"] = "unknown"
            if values:
                self.ctrl.update_announcement_attempt(
                    attempt.id, expected=terminal, updated_at=now, **values
                )
                logger.warning(
                    "Announcement attempt %s restoration interrupted by restart", attempt.id
                )
        if recovered:
            logger.warning("Marked %s interrupted announcement attempts unknown", recovered)
        self._recover_fallbacks(now)
        return recovered

    # Status ------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "synthesizer": self.synthesizer.status(),
            "cast": self.transport.status(),
            "media_origin": {
                "configured": bool(self.config.media_origin),
                "origin": self.config.media_origin,
            },
            "targets": [self._target_status(t) for t in self.config.targets],
            "discovery": self.discovery_status(),
            "defaults": {
                "language": DEFAULT_LANGUAGE,
                "languages": list(LANGUAGES),
                "volume_percent": DEFAULT_VOLUME_PERCENT,
                "chime_percent": DEFAULT_CHIME_PERCENT,
                "max_text_length": self.config.max_text_length,
            },
            "cache": self.cache.stats(),
            "schedules": {
                "quiet_hours": self.quiet_hours.text() if self.quiet_hours else None,
                "default_expiry_minutes": DEFAULT_EXPIRY_MINUTES,
                "timezone": "Europe/Helsinki",
            },
            "music_restoration": self.spotify.status(),
            "fallback": self.fallback_status(),
        }

    def _availability(self, target: SpeakerTarget) -> str:
        """Last known availability; never optimistic when not checked."""
        if not target.configured:
            return "unconfigured"
        probe = self._last_probe.get(target.key)
        if probe is None:
            return "not_checked"
        return "unavailable" if probe["state"] == "unavailable" else "available"

    def _target_status(self, target: SpeakerTarget) -> dict:
        data = {
            **target.public(),
            "availability": self._availability(target),
            "last_probe": self._last_probe.get(target.key),
        }
        if target.is_group:
            data["member_status"] = []
            for key in target.members:
                member = self.config.target(key)
                data["member_status"].append(
                    {
                        "key": key,
                        "name": member.name if member else key,
                        "availability": self._availability(member) if member else "unconfigured",
                        "last_probe": self._last_probe.get(key),
                    }
                )
        return data

    def _selectable_target(self, key) -> SpeakerTarget:
        """A configured target for a new test or schedule (input error otherwise)."""
        chosen = self.config.target(key) if isinstance(key, str) else None
        if chosen is None:
            names = ", ".join(t.name for t in self.config.targets)
            raise AnnouncementInputError("target", f"Choose a speaker: {names}.")
        if not chosen.configured:
            what = "speaker group" if chosen.is_group else "speaker"
            raise AnnouncementInputError(
                "target",
                f"{chosen.name}: this {what} is not configured yet. Find its Cast identity "
                "under Readiness and add it to ANNOUNCEMENT_TARGETS.",
            )
        return chosen

    # Browser preview (never dispatches) ---------------------------------------
    def request_preview(self, *, text, language=None, chime_percent=None, requested_by=None):
        content = self.content.prepare({"text": text, "language": language})
        chime = parse_percent(chime_percent, "chime_percent", DEFAULT_CHIME_PERCENT)
        now = self._clock()
        preview = {
            "id": secrets.token_urlsafe(12),
            "status": "preparing",
            "reason": None,
            "language": content.language,
            "voice": self.synthesizer.voice_for(content.language),
            "chime_percent": chime,
            "duration_s": None,
            "created_at": _iso(now),
            "_content": content,
            "_created": now,
            "_media_key": None,
            "_owner": requested_by,
        }
        with self._lock:
            self._prune_previews(now)
            self._previews[preview["id"]] = preview
        logger.debug("Preview %s requested language=%s", preview["id"], content.language)
        self._wake.set()
        return self._public_preview(preview)

    def get_preview(self, preview_id: str) -> dict | None:
        with self._lock:
            preview = self._previews.get(preview_id)
            return self._public_preview(preview) if preview else None

    def preview_audio(self, preview_id: str):
        with self._lock:
            preview = self._previews.get(preview_id)
            key = preview["_media_key"] if preview and preview["status"] == "ready" else None
        return self.cache.get(key) if key else None

    def _public_preview(self, preview: dict) -> dict:
        return {k: v for k, v in preview.items() if not k.startswith("_")}

    def _prune_previews(self, now: float) -> None:
        stale = [k for k, p in self._previews.items() if now - p["_created"] > PREVIEW_TTL_S]
        for key in stale:
            del self._previews[key]
        while len(self._previews) >= PREVIEW_LIMIT:
            del self._previews[next(iter(self._previews))]

    # Speaker tests -------------------------------------------------------------
    def request_speaker_test(
        self,
        *,
        text,
        language=None,
        target=None,
        volume_percent=None,
        chime_percent=None,
        request_id=None,
        requested_by=None,
    ) -> tuple[str, dict]:
        content = self.content.prepare({"text": text, "language": language})
        chosen = self._selectable_target(target)
        volume = parse_percent(volume_percent, "volume_percent", DEFAULT_VOLUME_PERCENT)
        chime = parse_percent(chime_percent, "chime_percent", DEFAULT_CHIME_PERCENT)
        if request_id is None:
            request_id = str(uuid4())
        elif not isinstance(request_id, str) or not 8 <= len(request_id) <= 64:
            raise AnnouncementInputError("request_id", "request_id must be 8-64 characters.")
        now = self._clock()
        test = {
            "id": str(uuid4()),
            "request_id": request_id,
            "requested_at": _iso(now),
            "requested_by": requested_by,
            "text": content.text,
            "language": content.language,
            "target_key": chosen.key,
            "volume_percent": volume,
            "chime_percent": chime,
        }
        attempt = self._new_attempt(
            chosen, content, volume=volume, chime=chime, now=now, speaker_test_id=test["id"]
        )
        with self._lock:
            if self.ctrl.announcement_request_consumed(request_id):
                raise AnnouncementInputError(
                    "request_id", "This speaker test request was deleted; use a new request id."
                )
            outcome, saved = self.ctrl.create_announcement_speaker_test(test, attempt)
        if outcome == "created":
            logger.info("Speaker test %s queued target=%s", saved.id, chosen.key)
            self._wake.set()
        return outcome, self.speaker_test_detail(saved.id)

    def delete_history(self, kind: str, identity: str) -> None:
        with self._lock:
            if (kind, identity) in self._finishing:
                raise AnnouncementStateError("history_active", "Restoration is still pending.")
            outcome = self.ctrl.delete_announcement_history(kind, identity)
            if outcome == "active":
                raise AnnouncementStateError(
                    "history_active",
                    "History cannot be deleted while speech, control, fallback or restoration work is pending.",
                )
        logger.info("Announcement history %s %s", identity, outcome)

    def _new_attempt(
        self,
        target: SpeakerTarget,
        content: PreparedContent,
        *,
        volume,
        chime,
        now,
        speaker_test_id=None,
        occurrence_id=None,
        priority=0,
        deadline=None,
    ) -> dict:
        return {
            "id": str(uuid4()),
            "speaker_test_id": speaker_test_id,
            "occurrence_id": occurrence_id,
            "target_key": target.key,
            "target_name": target.name,
            "target_host": target.host,
            "target_device_id": target.device_id,
            "speaker_set": sorted(target.speaker_set),
            "evidence_scope": "group" if target.kind == "group" else "device",
            "priority": priority,
            "text": content.text,
            "language": content.language,
            "volume_percent": volume,
            "chime_percent": chime,
            "status": "requested",
            "reason": None,
            "requested_at": _iso(now),
            "dispatch_deadline": deadline or _iso(now + self.config.queue_timeout_s),
            "updated_at": _iso(now),
        }

    def speaker_test_detail(self, test_id: str) -> dict | None:
        test = self.ctrl.get_announcement_speaker_test(test_id)
        if test is None:
            return None
        attempts = self.ctrl.list_announcement_attempts(speaker_test_ids=[test_id])
        return self._detail(test, attempts[0] if attempts else None)

    def list_speaker_tests(self, limit: int = 20) -> list[dict]:
        tests = self.ctrl.list_announcement_speaker_tests(limit)
        attempts = {
            a.speaker_test_id: a
            for a in self.ctrl.list_announcement_attempts(speaker_test_ids=[t.id for t in tests])
        }
        return [self._detail(t, attempts.get(t.id)) for t in tests]

    @staticmethod
    def _detail(test, attempt) -> dict:
        return {
            "kind": "speaker_test",
            "test": asdict(test),
            "attempt": asdict(attempt) if attempt else None,
        }

    def request_stop(self, test_id: str) -> dict:
        detail = self.speaker_test_detail(test_id)
        if detail is None or detail["attempt"] is None:
            raise AnnouncementStateError("not_found", "Speaker test not found.")
        attempt_id = detail["attempt"]["id"]
        now = _iso(self._clock())
        cancelled = self.ctrl.update_announcement_attempt(
            attempt_id,
            expected=("requested",),
            status="stopped",
            reason="cancelled_before_dispatch",
            stop_requested_at=now,
            stop_outcome="not_dispatched",
            stop_observed_at=now,
            finished_at=now,
            updated_at=now,
        )
        if not cancelled:
            requested = self.ctrl.update_announcement_attempt(
                attempt_id,
                expected=ACTIVE,
                stop_requested_at=now,
                stop_outcome=None,
                updated_at=now,
            )
            if not requested:
                raise AnnouncementStateError("already_finished", "This test has already ended.")
        logger.info("Stop requested for speaker test %s", test_id)
        self._wake.set()
        return self.speaker_test_detail(test_id)

    # Media capability ----------------------------------------------------------
    def open_media(self, token: str):
        key = self.grants.resolve(token)
        return self.cache.get(key) if key else None

    # Processing ----------------------------------------------------------------
    def tick(self) -> None:
        # Serialize processors, never owner requests, across external waits.
        with self._processing_lock:
            self._handle_stops()
            self._prepare_previews()
            self._poll_active()
            self._settle_occurrences()
            self._materialize_due()
            self._dispatch_work()
            self._process_fallbacks()
            self._run_discovery()
            self._evict_if_due()

    def _prepare(self, content: PreparedContent, chime: int) -> PreparedMedia:
        voice = self.synthesizer.voice_for(content.language)
        key = media_key(content, engine=self.synthesizer.engine, voice=voice, chime=chime)
        cached = self.cache.get(key)
        if cached is not None:
            return PreparedMedia(
                key=key, path=cached, duration_s=_wav_duration(cached) or 0.0, voice=voice
            )
        speech = self.synthesizer.synthesize(content.spoken_text, content.language)
        data, duration = compose(speech, chime)
        path = self.cache.put(key, data)
        return PreparedMedia(key=key, path=path, duration_s=duration, voice=voice)

    def _prepare_previews(self) -> None:
        with self._lock:
            pending = [p for p in self._previews.values() if p["status"] == "preparing"]
        for preview in pending:
            try:
                media = self._prepare(preview["_content"], preview["chime_percent"])
            except (SynthesisError, MediaError) as exc:
                with self._lock:
                    preview.update(status="failed", reason=exc.reason)
                logger.warning("Preview %s preparation failed: %s", preview["id"], exc.reason)
                continue
            with self._lock:
                preview.update(
                    status="ready",
                    voice=media.voice,
                    duration_s=round(media.duration_s, 2),
                    _media_key=media.key,
                )

    def _target(self, attempt) -> SpeakerTarget | None:
        return self.config.target(attempt.target_key)

    def _fail(self, attempt, reason: str, *, status="failed", expected=("requested",), **extra):
        now = _iso(self._clock())
        changed = self.ctrl.update_announcement_attempt(
            attempt.id,
            expected=expected,
            status=status,
            reason=reason,
            finished_at=now,
            updated_at=now,
            **extra,
        )
        if changed:
            logger.info("Announcement attempt %s %s: %s", attempt.id, status, reason)
        return changed

    def _dispatch_work(self) -> None:
        """Offer queued speaker tests and eligible occurrences in one order.

        Order is priority (urgent first), then request/due time. Work whose
        physical speakers overlap an active attempt or earlier queued work
        waits; nothing cuts off speech that is already playing.
        """
        now = self._clock()
        busy: set[str] = set()
        for active in self.ctrl.list_announcement_attempts(statuses=ACTIVE):
            busy |= set(active.speaker_set)
        work = [
            (-a.priority, a.requested_at, a.id, "attempt", a)
            for a in self.ctrl.list_announcement_attempts(statuses=("requested",))
        ]
        work += [
            (-self._occurrence_priority(o), o.next_attempt_at or o.due_at, o.id, "occurrence", o)
            for o in self._occurrence_candidates(now)
        ]
        work.sort(key=lambda item: item[:3])
        for *_order, kind, item in work:
            speakers = set(item.speaker_set)
            if speakers & busy:
                busy |= speakers  # keep order: later work on these speakers waits too
                if kind == "occurrence":
                    self._set_waiting(item, "speaker_busy", now)
                continue
            if kind == "attempt":
                busy |= speakers
                self._dispatch(item)
            elif self._dispatch_occurrence(item):
                busy |= speakers

    def _preflight(self, content: PreparedContent, chime: int):
        """Configuration and media checks before any probe: (failure, media)."""
        if not self.config.media_origin:
            return "media_origin_not_configured", None
        if not self.transport.status().get("available"):
            return "cast_unavailable", None
        try:
            return None, self._prepare(content, chime)
        except (SynthesisError, MediaError) as exc:
            return exc.reason, None

    def _probe_target(self, target: SpeakerTarget):
        probe = self.transport.probe(target)
        spotify_state = self.spotify.media_state(target)
        if probe.state != "unavailable" and spotify_state in ("playing", "unknown"):
            probe = replace(probe, state=spotify_state)
        if target.is_group:
            probe = self._probe_group(target, probe)
        now = self._clock()
        self._last_probe[target.key] = {
            "state": probe.state,
            "device_id": probe.device_id,
            "observed_at": _iso(now),
        }
        if probe.state in ("playing", "unknown"):
            self._media_busy_seen[target.key] = now
        return probe

    def _probe_group(self, group: SpeakerTarget, probe):
        """Add each member's own probe: availability and media state, never audibility."""
        member_probes = []
        for key in group.members:
            member = self.config.target(key)
            if member is None or not member.configured:
                member_probes.append((key, member.name if member else key, None, None))
                continue
            member_probes.append((key, member.name, member.device_id, self._probe_target(member)))
        return combine(probe, member_probes)

    def _member_device_ids(self, group: SpeakerTarget) -> dict:
        ids = {}
        for key in group.members:
            member = self.config.target(key)
            if member is not None and member.device_id:
                ids[key] = member.device_id
        return ids

    @staticmethod
    def _unavailable_reason(target: SpeakerTarget) -> str:
        return "group_unavailable" if target.is_group else "speaker_unavailable"

    @staticmethod
    def _unconfigured_reason(target: SpeakerTarget) -> str:
        return "group_not_configured" if target.is_group else "target_not_configured"

    def _music_settling(self, target: SpeakerTarget, now: float) -> bool:
        seen = self._media_busy_seen.get(target.key)
        return seen is not None and now - seen < self.config.music_idle_debounce_s

    @staticmethod
    def _media_values(target: SpeakerTarget, media: PreparedMedia, probe) -> dict:
        values = {
            "voice": media.voice,
            "media_key": media.key,
            "media_duration_s": round(media.duration_s, 3),
            "target_device_id": target.device_id or probe.device_id,
        }
        if target.is_group:
            values["member_evidence"] = [dict(m) for m in getattr(probe, "members", ())]
        return values

    def _dispatch(self, attempt) -> None:
        """Dispatch a queued speaker test (tests only play on an idle speaker)."""
        now = self._clock()
        if _iso(now) >= attempt.dispatch_deadline:
            self._fail(attempt, "queue_expired")
            return
        target = self._target(attempt)
        if target is None:
            self._fail(attempt, "target_not_configured")
            return
        if not target.configured:
            self._fail(attempt, self._unconfigured_reason(target))
            return
        content = PreparedContent(text=attempt.text, language=attempt.language)
        failure, media = self._preflight(content, attempt.chime_percent)
        if failure:
            self._fail(attempt, failure)
            return
        probe = self._probe_target(target)
        media_values = self._media_values(target, media, probe)
        if probe.state == "unavailable":
            # A group that does not answer fails visibly; its members are never
            # played separately as an unsynchronized substitute.
            self._fail(attempt, self._unavailable_reason(target), **media_values)
            return
        if target.device_id and probe.device_id and probe.device_id != target.device_id:
            self._fail(attempt, "device_identity_mismatch", **media_values)
            return
        if probe.state != "idle":
            reason = "speaker_playing" if probe.state == "playing" else "speaker_state_unknown"
            self._fail(attempt, reason, **media_values)
            return
        intent_at = _iso(self._clock())
        # Durable dispatch intent before any Cast effect.
        if not self.ctrl.update_announcement_attempt(
            attempt.id,
            expected=("requested",),
            require_no_stop=True,
            status="dispatching",
            intent_at=intent_at,
            volume_before=probe.volume,
            volume_applied=attempt.volume_percent / 100,
            updated_at=intent_at,
            **media_values,
        ):
            return
        self._send(self.ctrl.get_announcement_attempt(attempt.id), target, media, probe)

    def _dispatch_still_allowed(self, attempt) -> bool:
        latest = self.ctrl.get_announcement_attempt(attempt.id)
        occurrence = (
            self.ctrl.get_announcement_occurrence(attempt.occurrence_id)
            if attempt.occurrence_id
            else None
        )
        if latest is None or latest.status != "dispatching":
            return False
        if latest.stop_requested_at or (occurrence and occurrence.disposition):
            self._finish(
                attempt,
                "stopped",
                "cancelled_before_dispatch",
                stop_outcome="not_dispatched",
                stop_observed_at=_iso(self._clock()),
            )
            return False
        if _iso(self._clock()) >= attempt.dispatch_deadline:
            self._finish(attempt, "failed", "expired_before_dispatch", certain_not_played=True)
            return False
        return True

    def _send(self, attempt, target: SpeakerTarget, media: PreparedMedia, probe) -> None:
        """Cast effects for an attempt whose intent is already committed."""
        if not self._dispatch_still_allowed(attempt):
            return
        if attempt.interrupted_media:
            self.ctrl.update_announcement_attempt(
                attempt.id, media_restore="pending", updated_at=_iso(self._clock())
            )
            outcome = self.spotify.capture(attempt.id, target)
            self.ctrl.update_announcement_attempt(
                attempt.id, media_restore=outcome, updated_at=_iso(self._clock())
            )
        if not self._dispatch_still_allowed(attempt):
            return
        applied = attempt.volume_applied
        token = self.grants.issue(media.key, attempt.id, self.config.media_grant_ttl_s)
        url = f"{self.config.media_origin}{media_path(token)}"
        try:
            if probe.volume is None or abs(probe.volume - applied) > VOLUME_EPSILON:
                self.transport.set_volume(target, applied)
            if not self._dispatch_still_allowed(attempt):
                return
            self.transport.play(
                attempt.id, target, url, content_type=media.content_type, title="Announcement"
            )
            if target.is_group:
                for key in target.members:
                    member = self.config.target(key)
                    if member:
                        self.transport.watch_volume(attempt.id, member)
        except TransportError as exc:
            if exc.certain_not_played:
                self._finish(attempt, "failed", exc.reason, certain_not_played=True)
            else:
                self._finish(attempt, "unknown", exc.reason)
            return
        except Exception:
            logger.exception("Announcement dispatch %s raised unexpectedly", attempt.id)
            self._finish(attempt, "unknown", "dispatch_error")
            return
        now_iso = _iso(self._clock())
        self.ctrl.update_announcement_attempt(
            attempt.id,
            expected=("dispatching",),
            status="dispatched",
            dispatched_at=now_iso,
            updated_at=now_iso,
        )
        logger.info("Announcement attempt %s dispatched target=%s", attempt.id, target.key)

    def _finish(self, attempt, status: str, reason: str | None, **extra) -> None:
        identity = (
            ("occurrences", attempt.occurrence_id)
            if attempt.occurrence_id
            else ("tests", attempt.speaker_test_id)
        )
        with self._lock:
            self._finishing.add(identity)
        try:
            self._finish_pending(attempt, status, reason, **extra)
        finally:
            with self._lock:
                self._finishing.discard(identity)

    def _finish_pending(self, attempt, status: str, reason: str | None, **extra) -> None:
        now = _iso(self._clock())
        changed = self.ctrl.update_announcement_attempt(
            attempt.id,
            expected=ACTIVE,
            status=status,
            reason=reason,
            finished_at=now,
            updated_at=now,
            **extra,
        )
        self.grants.revoke_attempt(attempt.id)
        if not changed:
            self.transport.release(attempt.id)
            return
        logger.info("Announcement attempt %s %s reason=%s", attempt.id, status, reason)
        target = self._target(attempt)
        owned = bool(target and self.transport.owns(attempt.id, target, volume=True))
        if attempt.interrupted_media:
            probe = self.transport.probe(target) if target else None
            owned = bool(
                owned
                and probe
                and probe.state == "idle"
                and probe.device_id == attempt.target_device_id
                and probe.volume is not None
                and attempt.volume_applied is not None
                and abs(probe.volume - attempt.volume_applied) <= VOLUME_EPSILON
                and (
                    status == "finished"
                    or (status == "failed" and reason != "interrupted")
                    or (status == "stopped" and extra.get("stop_outcome") == "stopped")
                )
            )
        if attempt.interrupted_media and owned:
            owned = self.spotify.owns(attempt.id, attempt.volume_applied, target)
        volume_owned = self._restore_volume(attempt, owned=owned)
        outcome = self.spotify.restore(attempt.id, target, owned=owned and volume_owned)
        if (
            outcome is None
            and self.ctrl.get_announcement_attempt(attempt.id).media_restore == "pending"
        ):
            outcome = "unknown"
        if outcome:
            self.ctrl.update_announcement_attempt(attempt.id, media_restore=outcome, updated_at=now)
            logger.info("Announcement attempt %s music restore: %s", attempt.id, outcome)
        self.transport.release(attempt.id)
        if attempt.occurrence_id:
            self._settle_occurrence_of(attempt)

    def _restore_volume(self, attempt, *, owned=None) -> bool:
        target = self._target(attempt)
        before, applied = attempt.volume_before, attempt.volume_applied
        outcome = None
        if applied is None:
            return owned is not False
        if owned is False:
            outcome = "skipped_newer_intent"
        elif before is None or abs(before - applied) <= VOLUME_EPSILON:
            outcome = "not_needed"
        elif target is None:
            outcome = "unknown"
        else:
            probe = self.transport.probe(target)
            if probe.device_id != attempt.target_device_id:
                outcome = "skipped_newer_intent"
            elif probe.volume is None or probe.state in ("unknown", "unavailable"):
                outcome = "unknown"
            elif (
                probe.state != "idle"
                or abs(probe.volume - applied) > VOLUME_EPSILON
                # A status query can yield during an owner's change. Recheck
                # the retained subscription immediately before restoring.
                or not self.transport.owns(attempt.id, target, volume=True)
            ):
                outcome = "skipped_newer_intent"
            else:
                try:
                    self.transport.set_volume(target, before)
                    outcome = "restored"
                except TransportError:
                    outcome = "failed"
        now = _iso(self._clock())
        self.ctrl.update_announcement_attempt(
            attempt.id,
            volume_restore=outcome,
            volume_restored_at=now if outcome == "restored" else None,
            updated_at=now,
        )
        logger.info("Announcement attempt %s volume restore: %s", attempt.id, outcome)
        return outcome not in ("unknown", "skipped_newer_intent")

    def _handle_stops(self) -> None:
        for attempt in self.ctrl.list_announcement_attempts(statuses=("dispatched", "playing")):
            if not attempt.stop_requested_at or attempt.stop_outcome is not None:
                continue
            target = self._target(attempt)
            result = self.transport.stop(attempt.id, target) if target else "unknown"
            now = _iso(self._clock())
            if result == "stopped":
                self._finish(
                    attempt,
                    "stopped",
                    "stop_requested",
                    stop_outcome="stopped",
                    stop_observed_at=now,
                )
            else:
                self.ctrl.update_announcement_attempt(
                    attempt.id, expected=ACTIVE, stop_outcome=result, updated_at=now
                )
                logger.info("Stop for attempt %s observed as %s", attempt.id, result)

    def _poll_active(self) -> None:
        now = self._clock()
        for attempt in self.ctrl.list_announcement_attempts(statuses=("dispatched", "playing")):
            target = self._target(attempt)
            if target is None:
                self._finish(attempt, "unknown", "target_removed")
                continue
            observed = self.transport.poll(attempt.id, target)
            if target.is_group and attempt.member_evidence:
                self._observe_members(attempt, target, observed)
            state = observed.state
            if state == "playing" and attempt.status == "dispatched":
                self.ctrl.update_announcement_attempt(
                    attempt.id,
                    expected=("dispatched",),
                    status="playing",
                    playback_observed_at=_iso(now),
                    updated_at=_iso(now),
                )
            elif state == "finished":
                reason = None if attempt.playback_observed_at else "finished_without_observed_start"
                self._finish(attempt, "finished", reason)
            elif state == "failed":
                self._finish(attempt, "failed", observed.detail or "playback_failed")
            elif state == "interrupted":
                self._finish(attempt, "failed", "interrupted")
            elif state == "cancelled":
                extra = {}
                if attempt.stop_requested_at:
                    extra = {"stop_outcome": "stopped", "stop_observed_at": _iso(now)}
                reason = "stop_requested" if attempt.stop_requested_at else "stopped_externally"
                self._finish(attempt, "stopped", reason, **extra)
            elif state == "lost":
                self._finish(attempt, "unknown", "connection_lost")
            elif (
                attempt.status == "dispatched"
                and attempt.dispatched_at
                and (now - _epoch(attempt.dispatched_at) > self.config.playback_start_timeout_s)
            ):
                self._finish(attempt, "unknown", "no_playback_evidence")
            elif (
                attempt.status == "playing"
                and attempt.playback_observed_at
                and (
                    now - _epoch(attempt.playback_observed_at)
                    > (attempt.media_duration_s or 0) + self.config.playback_finish_grace_s
                )
            ):
                self._finish(attempt, "unknown", "no_finish_evidence")

    def _observe_members(self, attempt, group: SpeakerTarget, observed) -> None:
        updated = observe_playback(
            attempt.member_evidence, observed.member_ids, self._member_device_ids(group)
        )
        if updated is None:
            return
        if self.ctrl.update_announcement_attempt(
            attempt.id, expected=ACTIVE, member_evidence=updated, updated_at=_iso(self._clock())
        ):
            attempt.member_evidence = updated
            left = [m["key"] for m in updated if m["left_group_during_playback"]]
            if left:
                logger.info("Group attempt %s: members left during playback %s", attempt.id, left)

    def _evict_if_due(self) -> None:
        now = self._clock()
        if self._last_eviction is not None and now - self._last_eviction < EVICTION_INTERVAL_S:
            return
        self._last_eviction = now
        protected = self.grants.active_media_keys()
        with self._lock:
            protected |= {p["_media_key"] for p in self._previews.values() if p["_media_key"]}
        for attempt in self.ctrl.list_announcement_attempts(statuses=("requested", *ACTIVE)):
            if attempt.media_key:
                protected.add(attempt.media_key)
        self.cache.evict(protected)
