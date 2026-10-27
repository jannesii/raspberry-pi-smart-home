"""Announcement schedules and reminder occurrences (mixed into the service).

Owner operations validate input and record state only. ``tick()`` then:

1. settles occurrences whose attempt reached a terminal status;
2. materializes due schedule instances as occurrences (unique per
   schedule + instance due time, snapshotting the schedule);
3. expires waiting occurrences (``missed``) and holds them during quiet hours;
4. offers the rest to the dispatcher alongside speaker tests, where music,
   speaker availability and our own queue decide whether they wait.

Expiry is fixed at materialization (due + expiry minutes). Waiting, queueing
and probe retries never move it, and dispatch intent is committed together
with the occurrence transition, conditional on the occurrence still being
waiting, uncancelled and unexpired.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

from .config import DEFAULT_CHIME_PERCENT, DEFAULT_VOLUME_PERCENT, SpeakerTarget
from .content import (
    AnnouncementInputError,
    AnnouncementStateError,
    PreparedContent,
    parse_percent,
)
from .schedules import (
    QuietHours,
    epoch,
    helsinki_epoch,
    iso,
    next_instance,
    parse_expiry,
    parse_flag,
    upcoming_instances,
)

logger = logging.getLogger(__name__)

WAITING_HISTORY_LIMIT = 20
# Terminal attempt status -> occurrence delivery summary. Extension point for
# group/partial evidence (#37).
_DELIVERY = {"finished": "delivered", "failed": "failed", "unknown": "unknown"}
# Delivery outcomes that make an occurrence eligible for one fallback (#33).
_FALLBACK = {"failed": "failed", "partial": "partial", "unknown": "unknown"}


class OccurrenceMixin:
    """Schedule operations and occurrence processing for AnnouncementService."""

    # Owner operations -------------------------------------------------------------
    def _schedule_fields(self, payload: dict, now: float) -> dict:
        kind = payload.get("kind") or "one_time"
        if kind != "one_time":
            raise AnnouncementInputError("kind", "Only one-time schedules are available.")
        content = self.content.prepare(
            {"text": payload.get("text"), "language": payload.get("language")}
        )
        key = payload.get("target")
        target = self.config.target(key) if isinstance(key, str) else None
        if target is None:
            names = ", ".join(t.name for t in self.config.targets)
            raise AnnouncementInputError("target", f"Choose a speaker: {names}.")
        volume = parse_percent(
            payload.get("volume_percent"), "volume_percent", DEFAULT_VOLUME_PERCENT
        )
        chime = parse_percent(payload.get("chime_percent"), "chime_percent", DEFAULT_CHIME_PERCENT)
        due, fold = helsinki_epoch(payload.get("due_local"), payload.get("fold"))
        if due <= now:
            raise AnnouncementInputError("due_local", "Choose a time in the future.")
        return {
            "kind": kind,
            "text": content.text,
            "language": content.language,
            "target_key": target.key,
            "volume_percent": volume,
            "chime_percent": chime,
            "urgent": parse_flag(payload.get("urgent"), "urgent"),
            "quiet_hours_override": parse_flag(
                payload.get("quiet_hours_override"), "quiet_hours_override"
            ),
            "expiry_minutes": parse_expiry(payload.get("expiry_minutes")),
            "due_local": payload["due_local"],
            "due_fold": fold,
            "due_at": iso(due),
        }

    def create_schedule(self, payload: dict, *, request_id=None, requested_by=None):
        now = self._clock()
        fields = self._schedule_fields(payload, now)
        if request_id is not None and (
            not isinstance(request_id, str) or not 8 <= len(request_id) <= 64
        ):
            raise AnnouncementInputError("request_id", "request_id must be 8-64 characters.")
        row = {
            "id": str(uuid4()),
            "request_id": request_id,
            "status": "active",
            "revision": 1,
            "max_announcements": 1,
            "repeat_interval_minutes": None,
            "recurrence": None,
            "created_at": iso(now),
            "created_by": requested_by,
            "updated_at": iso(now),
            "paused_at": None,
            "deleted_at": None,
            **fields,
        }
        nxt = next_instance(SimpleNamespace(**row), now)
        row["next_due_at"] = iso(nxt) if nxt is not None else None
        outcome, saved = self.ctrl.create_announcement_schedule(row)
        if outcome == "created":
            logger.info(
                "Announcement schedule %s saved kind=%s target=%s",
                saved.id,
                saved.kind,
                saved.target_key,
            )
            self._wake.set()
        return outcome, self.schedule_detail(saved.id)

    def _existing_schedule(self, schedule_id: str):
        schedule = self.ctrl.get_announcement_schedule(schedule_id)
        if schedule is None:
            raise self._state_error("not_found", "Schedule not found.")
        return schedule

    @staticmethod
    def _state_error(code, message):
        return AnnouncementStateError(code, message)

    def update_schedule(self, schedule_id: str, payload: dict, *, revision=None) -> dict:
        """Edit a schedule; existing occurrences keep their snapshot."""
        now = self._clock()
        schedule = self._existing_schedule(schedule_id)
        if schedule.status == "deleted":
            raise self._state_error("deleted", "This schedule was deleted.")
        if revision is not None and revision != schedule.revision:
            raise self._state_error(
                "stale_revision", "The schedule changed meanwhile; reload it before saving."
            )
        fields = self._schedule_fields(payload, now)
        if all(getattr(schedule, k) == v for k, v in fields.items()):
            return self.schedule_detail(schedule_id)
        values = {**fields, "revision": schedule.revision + 1, "updated_at": iso(now)}
        if schedule.status == "active":
            nxt = next_instance(SimpleNamespace(**fields), now)
            values["next_due_at"] = iso(nxt) if nxt is not None else None
        if not self.ctrl.update_announcement_schedule(
            schedule_id,
            expected_status=("active", "paused"),
            expected_revision=schedule.revision,
            **values,
        ):
            raise self._state_error(
                "stale_revision", "The schedule changed meanwhile; reload it before saving."
            )
        logger.info("Announcement schedule %s edited revision=%s", schedule_id, values["revision"])
        self._wake.set()
        return self.schedule_detail(schedule_id)

    def pause_schedule(self, schedule_id: str) -> dict:
        now = iso(self._clock())
        schedule = self._existing_schedule(schedule_id)
        if schedule.status == "deleted":
            raise self._state_error("deleted", "This schedule was deleted.")
        if self.ctrl.update_announcement_schedule(
            schedule_id,
            expected_status=("active",),
            cancel_reason="schedule_paused",
            status="paused",
            paused_at=now,
            next_due_at=None,
            updated_at=now,
        ):
            logger.info("Announcement schedule %s paused", schedule_id)
        return self.schedule_detail(schedule_id)

    def resume_schedule(self, schedule_id: str) -> dict:
        """Resume future instances only; cancelled or passed ones stay as they are."""
        now = self._clock()
        schedule = self._existing_schedule(schedule_id)
        if schedule.status == "deleted":
            raise self._state_error("deleted", "This schedule was deleted.")
        nxt = next_instance(schedule, now)
        if self.ctrl.update_announcement_schedule(
            schedule_id,
            expected_status=("paused",),
            status="active",
            paused_at=None,
            next_due_at=iso(nxt) if nxt is not None else None,
            updated_at=iso(now),
        ):
            logger.info(
                "Announcement schedule %s resumed has_next=%s", schedule_id, nxt is not None
            )
            self._wake.set()
        return self.schedule_detail(schedule_id)

    def delete_schedule(self, schedule_id: str) -> dict:
        """Stop future and pending speech; history (occurrences, attempts) stays."""
        now = iso(self._clock())
        self._existing_schedule(schedule_id)
        if self.ctrl.update_announcement_schedule(
            schedule_id,
            expected_status=("active", "paused"),
            cancel_reason="schedule_deleted",
            status="deleted",
            deleted_at=now,
            next_due_at=None,
            updated_at=now,
        ):
            logger.info("Announcement schedule %s deleted (history kept)", schedule_id)
        return self.schedule_detail(schedule_id)

    # Reads ----------------------------------------------------------------------
    def _schedule_public(self, schedule) -> dict:
        target = self.config.target(schedule.target_key)
        data = asdict(schedule)
        data["target_name"] = target.name if target else schedule.target_key
        data["due_passed"] = (
            schedule.status != "deleted"
            and schedule.kind == "one_time"
            and schedule.next_due_at is None
            and schedule.due_at is not None
            and epoch(schedule.due_at) <= self._clock()
        )
        return data

    def schedule_detail(self, schedule_id: str) -> dict | None:
        schedule = self.ctrl.get_announcement_schedule(schedule_id)
        if schedule is None:
            return None
        occurrences = self.ctrl.list_announcement_occurrences(
            schedule_ids=[schedule_id], newest_first=True, limit=20
        )
        return {
            "schedule": self._schedule_public(schedule),
            "upcoming": self._upcoming(schedule),
            "occurrences": self._occurrence_details(occurrences),
        }

    def _upcoming(self, schedule) -> list[dict]:
        if schedule.status != "active" or schedule.next_due_at is None:
            return []
        first = epoch(schedule.next_due_at)
        instances = [first, *upcoming_instances(schedule, first, limit=4)]
        return [{"due_at": iso(at)} for at in instances]

    def list_schedules(self) -> list[dict]:
        schedules = self.ctrl.list_announcement_schedules()
        return [
            {"schedule": self._schedule_public(s), "upcoming": self._upcoming(s)} for s in schedules
        ]

    def _occurrence_details(self, occurrences) -> list[dict]:
        attempts: dict[str, list] = {}
        if occurrences:
            for attempt in self.ctrl.list_announcement_attempts(
                occurrence_ids=[o.id for o in occurrences]
            ):
                attempts.setdefault(attempt.occurrence_id, []).append(asdict(attempt))
        for items in attempts.values():
            items.sort(key=lambda a: a["requested_at"])
        return [
            {
                "kind": "occurrence",
                "occurrence": asdict(o),
                "attempts": attempts.get(o.id, []),
                # Reminder acknowledgement (Done) is recorded separately (#35);
                # delivery evidence never implies it.
                "acknowledgement": None,
            }
            for o in occurrences
        ]

    def list_occurrences(self, limit: int = 50) -> list[dict]:
        occurrences = self.ctrl.list_announcement_occurrences(newest_first=True, limit=limit)
        return self._occurrence_details(occurrences)

    def occurrence_detail(self, occurrence_id: str) -> dict | None:
        occurrence = self.ctrl.get_announcement_occurrence(occurrence_id)
        return self._occurrence_details([occurrence])[0] if occurrence else None

    # Processing ---------------------------------------------------------------------
    def _materialize_due(self) -> None:
        now = self._clock()
        for schedule in self.ctrl.list_due_announcement_schedules(iso(now)):
            due = epoch(schedule.next_due_at)
            target = self.config.target(schedule.target_key)
            quiet = None if schedule.quiet_hours_override else self.quiet_hours
            nxt = next_instance(schedule, due)
            occurrence = {
                "id": str(uuid4()),
                "schedule_id": schedule.id,
                "instance_due_at": schedule.next_due_at,
                "schedule_revision": schedule.revision,
                "created_at": iso(now),
                "text": schedule.text,
                "language": schedule.language,
                "target_key": schedule.target_key,
                "target_name": target.name if target else schedule.target_key,
                "speaker_set": sorted(target.speaker_set) if target else [schedule.target_key],
                "volume_percent": schedule.volume_percent,
                "chime_percent": schedule.chime_percent,
                "urgent": schedule.urgent,
                "quiet_hours": quiet.text() if quiet else None,
                "max_announcements": schedule.max_announcements,
                "repeat_interval_minutes": schedule.repeat_interval_minutes,
                "due_at": schedule.next_due_at,
                "expires_at": iso(due + schedule.expiry_minutes * 60),
                "next_attempt_at": schedule.next_due_at,
                "status": "waiting",
                "waiting_reason": None,
                "waiting_since": None,
                "waiting_history": [],
                "announcements_started": 0,
                "current_attempt_id": None,
                "delivery": "none",
                "updated_at": iso(now),
            }
            if self.ctrl.materialize_announcement_occurrence(
                occurrence, next_due_at=iso(nxt) if nxt is not None else None
            ):
                logger.info(
                    "Occurrence %s created for schedule %s due=%s",
                    occurrence["id"],
                    schedule.id,
                    schedule.next_due_at,
                )

    def _set_waiting(self, occurrence, reason: str, now: float) -> None:
        if occurrence.waiting_reason == reason:
            return
        history = [*occurrence.waiting_history, {"reason": reason, "since": iso(now)}]
        if self.ctrl.update_announcement_occurrence(
            occurrence.id,
            expected=("waiting",),
            waiting_reason=reason,
            waiting_since=iso(now),
            waiting_history=history[-WAITING_HISTORY_LIMIT:],
            updated_at=iso(now),
        ):
            occurrence.waiting_reason = reason
            logger.info("Occurrence %s waiting: %s", occurrence.id, reason)

    def _expire(self, occurrence, now: float) -> None:
        values = {
            "status": "closed",
            "waiting_reason": None,
            "disposition": "missed",
            "disposition_reason": occurrence.waiting_reason or "expired_before_dispatch",
            "closed_at": iso(now),
            "updated_at": iso(now),
        }
        if occurrence.delivery != "delivered":
            values.update(fallback_reason="expired_undelivered", fallback_eligible_at=iso(now))
        if self.ctrl.update_announcement_occurrence(occurrence.id, expected=("waiting",), **values):
            self._next_probe.pop(occurrence.id, None)
            logger.info(
                "Occurrence %s expired undelivered: %s", occurrence.id, values["disposition_reason"]
            )

    def _quiet(self, occurrence, now: float) -> bool:
        quiet = QuietHours.parse(occurrence.quiet_hours)
        return bool(quiet and quiet.contains(now))

    def _occurrence_candidates(self, now: float) -> list:
        """Waiting occurrences that time policy allows to speak right now."""
        eligible = []
        for occurrence in self.ctrl.list_announcement_occurrences(statuses=("waiting",)):
            if now >= epoch(occurrence.expires_at):
                self._expire(occurrence, now)
            elif occurrence.next_attempt_at and now < epoch(occurrence.next_attempt_at):
                continue
            elif self._quiet(occurrence, now):
                self._set_waiting(occurrence, "quiet_hours", now)
            else:
                eligible.append(occurrence)
        return eligible

    @staticmethod
    def _occurrence_priority(occurrence) -> int:
        return 1 if occurrence.urgent else 0

    def _occurrence_target(self, occurrence) -> SpeakerTarget | None:
        return self.config.target(occurrence.target_key)

    def _fail_occurrence(self, occurrence, target, reason: str, **media_values) -> None:
        """Record a definitive pre-dispatch failure as an attempt and close."""
        now = self._clock()
        content = PreparedContent(text=occurrence.text, language=occurrence.language)
        attempt = self._new_attempt(
            target,
            content,
            volume=occurrence.volume_percent,
            chime=occurrence.chime_percent,
            now=now,
            occurrence_id=occurrence.id,
            priority=self._occurrence_priority(occurrence),
            deadline=occurrence.expires_at,
        )
        attempt.update(status="failed", reason=reason, finished_at=iso(now), **media_values)
        if self.ctrl.add_announcement_occurrence_attempt(
            occurrence.id,
            attempt,
            status="closed",
            waiting_reason=None,
            current_attempt_id=attempt["id"],
            delivery="failed",
            delivery_reason=reason,
            closed_at=iso(now),
            fallback_reason="failed",
            fallback_eligible_at=iso(now),
            updated_at=iso(now),
        ):
            self._next_probe.pop(occurrence.id, None)
            logger.info("Occurrence %s failed before dispatch: %s", occurrence.id, reason)

    def _dispatch_occurrence(self, occurrence) -> bool:
        """Try to start speech for an eligible occurrence; True if dispatched."""
        now = self._clock()
        target = self._occurrence_target(occurrence)
        if target is None:
            placeholder = SpeakerTarget(
                key=occurrence.target_key,
                name=occurrence.target_name,
                host=None,
                members=tuple(occurrence.speaker_set),
            )
            self._fail_occurrence(occurrence, placeholder, "target_not_configured")
            return False
        if self._next_probe.get(occurrence.id, 0.0) > now:
            return False
        content = PreparedContent(text=occurrence.text, language=occurrence.language)
        failure, media = self._preflight(content, occurrence.chime_percent)
        if failure:
            self._fail_occurrence(occurrence, target, failure)
            return False
        probe = self._probe_target(target)
        self._next_probe[occurrence.id] = now + self.config.wait_probe_interval_s
        media_values = self._media_values(target, media, probe)
        if probe.state == "unavailable":
            self._set_waiting(occurrence, "speaker_unavailable", now)
            return False
        if target.device_id and probe.device_id and probe.device_id != target.device_id:
            self._fail_occurrence(occurrence, target, "device_identity_mismatch", **media_values)
            return False
        interrupted = None
        if probe.state != "idle":
            if not occurrence.urgent:
                reason = "music_playing" if probe.state == "playing" else "media_unknown"
                self._set_waiting(occurrence, reason, now)
                return False
            interrupted = probe.state  # urgent may replace music; quiet hours already held
        elif not occurrence.urgent and self._music_settling(target, now):
            self._set_waiting(occurrence, "music_playing", now)  # e.g. between tracks
            return False
        intent = self._clock()
        if self._quiet(occurrence, intent):
            self._set_waiting(occurrence, "quiet_hours", intent)
            return False
        attempt = self._new_attempt(
            target,
            content,
            volume=occurrence.volume_percent,
            chime=occurrence.chime_percent,
            now=intent,
            occurrence_id=occurrence.id,
            priority=self._occurrence_priority(occurrence),
            deadline=occurrence.expires_at,
        )
        attempt.update(
            status="dispatching",
            intent_at=iso(intent),
            volume_before=probe.volume,
            volume_applied=occurrence.volume_percent / 100,
            interrupted_media=interrupted,
            # Restoring interrupted music needs the optional Spotify link (#38).
            media_restore="unavailable" if interrupted else None,
            **media_values,
        )
        if not self.ctrl.add_announcement_occurrence_attempt(
            occurrence.id,
            attempt,
            not_expired_at=iso(intent),
            status="announcing",
            waiting_reason=None,
            current_attempt_id=attempt["id"],
            announcements_started=occurrence.announcements_started + 1,
            updated_at=iso(intent),
        ):
            logger.info("Occurrence %s no longer eligible at dispatch", occurrence.id)
            return False
        self._next_probe.pop(occurrence.id, None)
        if interrupted:
            logger.info(
                "Urgent occurrence %s interrupting media state=%s", occurrence.id, interrupted
            )
        self._send(self.ctrl.get_announcement_attempt(attempt["id"]), target, media, probe)
        return True

    def _settle_occurrences(self) -> None:
        """Apply terminal attempt outcomes to announcing occurrences.

        Idempotent and crash-safe: also reconciles attempts that a restart
        marked unknown before their occurrence was updated.
        """
        announcing = self.ctrl.list_announcement_occurrences(statuses=("announcing",))
        if not announcing:
            return
        attempts = {
            a.id: a
            for a in self.ctrl.list_announcement_attempts(occurrence_ids=[o.id for o in announcing])
        }
        for occurrence in announcing:
            attempt = attempts.get(occurrence.current_attempt_id)
            if attempt is not None and attempt.status in (
                "finished",
                "failed",
                "unknown",
                "stopped",
            ):
                self._settle(occurrence, attempt)

    def _settle_occurrence_of(self, attempt) -> None:
        occurrence = self.ctrl.get_announcement_occurrence(attempt.occurrence_id)
        latest = self.ctrl.get_announcement_attempt(attempt.id)
        if occurrence and latest and occurrence.status == "announcing":
            self._settle(occurrence, latest)

    def _settle(self, occurrence, attempt) -> None:
        """Close an occurrence from its attempt's evidence.

        Extension points: repeats (#35) would return the occurrence to
        ``waiting`` with a later ``next_attempt_at`` instead of closing it;
        Stop speaking (#35) sets disposition 'stopped' (no fallback).
        """
        now = iso(self._clock())
        disposition = occurrence.disposition
        disposition_reason = occurrence.disposition_reason
        if attempt.status == "stopped":
            if attempt.stop_requested_at:
                delivery = "delivered" if attempt.playback_observed_at else "none"
                disposition = disposition or "stopped"
                disposition_reason = disposition_reason or "stop_requested"
            else:
                delivery = "unknown"
        else:
            delivery = _DELIVERY[attempt.status]
        values = {
            "status": "closed",
            "delivery": delivery,
            "delivery_reason": attempt.reason,
            "disposition": disposition,
            "disposition_reason": disposition_reason,
            "closed_at": now,
            "updated_at": now,
        }
        fallback = _FALLBACK.get(delivery)
        if fallback and disposition is None:
            values.update(fallback_reason=fallback, fallback_eligible_at=now)
        if self.ctrl.update_announcement_occurrence(
            occurrence.id, expected=("announcing",), **values
        ):
            logger.info(
                "Occurrence %s closed delivery=%s fallback=%s",
                occurrence.id,
                delivery,
                values.get("fallback_reason"),
            )
