"""Announcement schedules and reminder occurrences (mixed into the service).

Owner operations validate input and record state only. ``tick()`` then:

1. settles occurrences whose attempt reached a terminal status;
2. materializes due schedule instances as occurrences (unique per
   schedule + instance due time, snapshotting the schedule);
3. expires waiting occurrences (``missed``) and holds them during quiet hours;
4. offers the rest to the dispatcher alongside speaker tests, where music,
   speaker availability and our own queue decide whether they wait.

Expiry starts at materialization (due + expiry minutes); only deliberate
snooze extends it. Waiting, queueing and probe retries never move it. Dispatch
intent is committed together with the occurrence transition, conditional on it being
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
from .groups import MEMBER_UNAVAILABLE, affected_members
from .schedules import (
    Instance,
    QuietHours,
    epoch,
    helsinki_epoch,
    helsinki_label,
    instance_at,
    iso,
    next_instance,
    parse_expiry,
    parse_flag,
    parse_recurrence,
    parse_repeats,
    repeat_fit,
    upcoming_instances,
)

logger = logging.getLogger(__name__)

WAITING_HISTORY_LIMIT = 20
# Passed instances materialized per schedule per tick when catching up.
MATERIALIZE_BATCH = 50
# Terminal attempt status -> occurrence delivery summary; a group attempt
# with a member known to be missing is downgraded to 'partial' in _settle.
_DELIVERY = {"finished": "delivered", "failed": "failed", "unknown": "unknown"}
# Delivery outcomes that make an occurrence eligible for one fallback (#33).
_FALLBACK = {"failed": "failed", "partial": "partial", "unknown": "unknown"}


class OccurrenceMixin:
    """Schedule operations and occurrence processing for AnnouncementService."""

    # Owner operations -------------------------------------------------------------
    def _schedule_fields(self, payload: dict, now: float) -> dict:
        kind = payload.get("kind") or "one_time"
        if kind not in ("one_time", "recurring"):
            raise AnnouncementInputError("kind", "Choose a one-time or a recurring reminder.")
        content = self.content.prepare(
            {"text": payload.get("text"), "language": payload.get("language")}
        )
        target = self._selectable_target(payload.get("target"))
        volume = parse_percent(
            payload.get("volume_percent"), "volume_percent", DEFAULT_VOLUME_PERCENT
        )
        chime = parse_percent(payload.get("chime_percent"), "chime_percent", DEFAULT_CHIME_PERCENT)
        count, interval = parse_repeats(
            payload.get("max_announcements"), payload.get("repeat_interval_minutes")
        )
        when = {"due_local": None, "due_fold": None, "due_at": None, "recurrence": None}
        if kind == "recurring":
            when["recurrence"] = parse_recurrence(payload.get("recurrence"))
        else:
            due, fold = helsinki_epoch(payload.get("due_local"), payload.get("fold"))
            if due <= now:
                raise AnnouncementInputError("due_local", "Choose a time in the future.")
            when.update(due_local=payload["due_local"], due_fold=fold, due_at=iso(due))
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
            "max_announcements": count,
            "repeat_interval_minutes": interval,
            **when,
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
        data["repeats"] = repeat_fit(
            schedule.max_announcements, schedule.repeat_interval_minutes, schedule.expiry_minutes
        )
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
        head = instance_at(schedule, first) or Instance(first, helsinki_label(first))
        items = []
        for instance in [head, *upcoming_instances(schedule, first, limit=4)]:
            item = {"due_at": iso(instance.at)}
            if instance.dst_gap:
                # Shown so the owner sees it will be recorded missed, not spoken.
                item.update(local=instance.local, missed="dst_gap")
            items.append(item)
        return items

    def list_schedules(self) -> list[dict]:
        schedules = self.ctrl.list_announcement_schedules()
        return [
            {"schedule": self._schedule_public(s), "upcoming": self._upcoming(s)} for s in schedules
        ]

    def _occurrence_details(self, occurrences) -> list[dict]:
        attempts: dict[str, list] = {}
        acks = {}
        snoozes = {}
        if occurrences:
            ids = [o.id for o in occurrences]
            for snooze in self.ctrl.list_announcement_snoozes(ids):
                snoozes.setdefault(snooze["occurrence_id"], []).append(snooze)
            for attempt in self.ctrl.list_announcement_attempts(occurrence_ids=ids):
                attempts.setdefault(attempt.occurrence_id, []).append(asdict(attempt))
            acks = {
                a.occurrence_id: asdict(a)
                for a in self.ctrl.list_announcement_acknowledgements(ids)
            }
        for items in attempts.values():
            items.sort(key=lambda a: a["requested_at"])
        fallbacks = self._fallbacks_by_occurrence([o.id for o in occurrences])
        return [
            {
                "kind": "occurrence",
                "occurrence": {
                    **asdict(o),
                    "instance_local": helsinki_label(
                        epoch(o.instance_due_at), dst_gap=o.disposition_reason == "dst_gap"
                    ),
                },
                "attempts": attempts.get(o.id, []),
                # Fallback notification (#33): its own evidence, never acknowledgement.
                "fallback": fallbacks.get(o.id),
                # The owner's Done/Skip, kept apart from delivery evidence:
                # playback or a fallback never implies it.
                "acknowledgement": acks.get(o.id),
                "snoozes": snoozes.get(o.id, []),
                "actions": self._actions(o, attempts.get(o.id, []), acks.get(o.id)),
            }
            for o in occurrences
        ]

    def _actions(self, occurrence, attempts: list[dict], acknowledgement) -> list[str]:
        """Owner actions that would change something now (for the page)."""
        actions = [] if acknowledgement else ["done", "skip"]
        current = next((a for a in attempts if a["id"] == occurrence.current_attempt_id), None)
        speaking = (
            occurrence.status == "announcing"
            and current is not None
            and current["status"] in ("dispatching", "dispatched", "playing")
            and current["stop_requested_at"] is None
        )
        pending_follow_up = (
            occurrence.status == "waiting"
            and occurrence.disposition is None
            and occurrence.announcements_started + occurrence.retries_started > 0
        )
        if speaking or pending_follow_up:
            actions.append("stop")
        schedule = self.ctrl.get_announcement_schedule(occurrence.schedule_id)
        if (
            not acknowledgement
            and occurrence.disposition in (None, "stopped")
            and schedule
            and schedule.status == "active"
            and (schedule.next_due_at is None or epoch(schedule.next_due_at) > self._clock())
        ):
            newer = self.ctrl.has_newer_announcement_occurrence(
                occurrence.schedule_id, occurrence.instance_due_at
            )
            if not newer:
                actions.append("snooze")
        return actions

    # Owner actions on occurrences ---------------------------------------------------
    def acknowledge_occurrence(self, occurrence_id: str, action: str, *, by=None) -> dict:
        """Done ('done') or Skip this occurrence ('skipped'); idempotent per action.

        Records the owner's handling of this reminder only: never a medicine
        dose, blood-pressure value or delivery evidence.
        """
        if action not in ("done", "skipped"):
            raise self._state_error("not_found", "Unknown occurrence action.")
        now = iso(self._clock())
        outcome, _ack = self.ctrl.acknowledge_announcement_occurrence(
            occurrence_id, action=action, ack_id=str(uuid4()), at=now, by=by
        )
        if outcome == "not_found":
            raise self._state_error("not_found", "Occurrence not found.")
        if outcome == "conflict":
            raise self._state_error(
                "already_acknowledged", "This reminder was already marked differently."
            )
        if outcome == "created":
            self._next_probe.pop(occurrence_id, None)
            logger.info("Occurrence %s acknowledged: %s", occurrence_id, action)
            self._wake.set()
        return self.occurrence_detail(occurrence_id)

    def snooze_occurrence(
        self, occurrence_id: str, *, minutes=10, request_id=None, by=None
    ) -> dict:
        """Explicit postponement, independent of automatic announcement budgets."""
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or minutes not in (5, 10, 15, 30)
        ):
            raise AnnouncementInputError("minutes", "Choose 5, 10, 15 or 30 minutes.")
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 64:
            raise AnnouncementInputError("request_id", "request_id must be 8-64 characters.")
        occurrence = self.ctrl.get_announcement_occurrence(occurrence_id)
        if occurrence is None:
            raise self._state_error("not_found", "Occurrence not found.")
        prior = self.ctrl.list_announcement_snoozes([occurrence_id])
        windows = prior or [
            {"prior_due_at": occurrence.due_at, "prior_expires_at": occurrence.expires_at}
        ]
        # A shorter second snooze can retain an already extended deadline.
        # The smallest recorded window is the original snapshot, even when
        # requests share a clock timestamp and sort by request id.
        window = min(epoch(s["prior_expires_at"]) - epoch(s["prior_due_at"]) for s in windows)
        now = self._clock()
        due = now + minutes * 60
        row = {
            "request_id": request_id,
            "occurrence_id": occurrence_id,
            "snoozed_at": iso(now),
            "snoozed_by": by,
            "minutes": minutes,
            "prior_disposition": occurrence.disposition,
            "prior_due_at": occurrence.due_at,
            "prior_expires_at": occurrence.expires_at,
            "due_at": iso(due),
            "expires_at": iso(max(epoch(occurrence.expires_at), due + window)),
        }
        outcome = self.ctrl.snooze_announcement_occurrence(row, expected_due_at=occurrence.due_at)
        if outcome == "not_found":
            raise self._state_error("not_found", "Occurrence not found.")
        if outcome in ("ineligible", "conflict"):
            raise self._state_error(
                outcome, "This reminder changed or is no longer eligible for snooze."
            )
        if outcome == "created":
            self._next_probe.pop(occurrence_id, None)
            logger.info(
                "Occurrence %s snoozed minutes=%s due=%s", occurrence_id, minutes, row["due_at"]
            )
            self._wake.set()
        return self.occurrence_detail(occurrence_id)

    def stop_occurrence(self, occurrence_id: str) -> dict:
        """Stop speaking: end current speech and further automatic repeats.

        Not an acknowledgement; Done and Skip stay available. The actual stop
        outcome is what the speaker reports (attempt ``stop_outcome``).
        """
        occurrence = self.ctrl.get_announcement_occurrence(occurrence_id)
        if occurrence is None:
            raise self._state_error("not_found", "Occurrence not found.")
        now = iso(self._clock())
        stopped_attempt = False
        if occurrence.current_attempt_id and occurrence.status == "announcing":
            stopped_attempt = self.ctrl.update_announcement_attempt(
                occurrence.current_attempt_id,
                expected=("dispatching", "dispatched", "playing"),
                stop_requested_at=now,
                stop_outcome=None,
                updated_at=now,
            )
        applied = self.ctrl.stop_announcement_occurrence(occurrence_id, at=now)
        if not stopped_attempt and applied is None:
            raise self._state_error(
                "not_speaking", "Nothing is being spoken or repeated for this reminder."
            )
        self._next_probe.pop(occurrence_id, None)
        logger.info(
            "Stop speaking for occurrence %s (current speech=%s, follow-ups=%s)",
            occurrence_id,
            stopped_attempt,
            applied,
        )
        self._wake.set()
        return self.occurrence_detail(occurrence_id)

    def list_occurrences(self, limit: int = 50) -> list[dict]:
        occurrences = self.ctrl.list_announcement_occurrences(newest_first=True, limit=limit)
        return self._occurrence_details(occurrences)

    def occurrence_detail(self, occurrence_id: str) -> dict | None:
        occurrence = self.ctrl.get_announcement_occurrence(occurrence_id)
        return self._occurrence_details([occurrence])[0] if occurrence else None

    # Processing ---------------------------------------------------------------------
    def _materialize_due(self) -> None:
        """Create occurrences for every passed instance, oldest first.

        After downtime each passed instance still gets its own occurrence so
        history shows what was missed; the expiry check that follows closes
        the stale ones as ``missed`` before anything is spoken, so only
        instances still inside their window can speak (no burst). Catch-up
        is bounded per tick; the next tick continues.
        """
        now = self._clock()
        for schedule in self.ctrl.list_due_announcement_schedules(iso(now)):
            cursor = schedule.next_due_at
            for _ in range(MATERIALIZE_BATCH):
                due = epoch(cursor)
                instance = instance_at(schedule, due)
                nxt = next_instance(schedule, due)
                occurrence = self._occurrence_row(schedule, cursor, now, instance)
                if not self.ctrl.materialize_announcement_occurrence(
                    occurrence, next_due_at=iso(nxt) if nxt is not None else None
                ):
                    break  # paused, edited or materialized elsewhere meanwhile
                logger.info(
                    "Occurrence %s created for schedule %s due=%s%s",
                    occurrence["id"],
                    schedule.id,
                    cursor,
                    " (missed: clock change)" if occurrence["status"] == "closed" else "",
                )
                if nxt is None or nxt > now:
                    break
                cursor = iso(nxt)

    def _occurrence_row(self, schedule, due_iso: str, now: float, instance) -> dict:
        due = epoch(due_iso)
        target = self.config.target(schedule.target_key)
        quiet = None if schedule.quiet_hours_override else self.quiet_hours
        row = {
            "id": str(uuid4()),
            "schedule_id": schedule.id,
            "instance_due_at": due_iso,
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
            "due_at": due_iso,
            "expires_at": iso(due + schedule.expiry_minutes * 60),
            "next_attempt_at": due_iso,
            "status": "waiting",
            "waiting_reason": None,
            "waiting_since": None,
            "waiting_history": [],
            "announcements_started": 0,
            "current_attempt_id": None,
            "delivery": "none",
            "updated_at": iso(now),
        }
        if instance is not None and instance.dst_gap:
            # The wall time did not exist (spring clock change): never spoken,
            # recorded as a missed reminder so a fallback can tell the owner.
            row.update(
                status="closed",
                next_attempt_at=None,
                disposition="missed",
                disposition_reason="dst_gap",
                closed_at=iso(now),
                fallback_reason="expired_undelivered",
                fallback_eligible_at=iso(now),
            )
        return row

    def _set_waiting(self, occurrence, reason: str, now: float) -> None:
        if occurrence.waiting_reason == reason:
            return
        history = [*occurrence.waiting_history, {"reason": reason, "since": iso(now)}]
        if self.ctrl.update_announcement_occurrence(
            occurrence.id,
            expected=("waiting",),
            snapshot={"due_at": occurrence.due_at},
            waiting_reason=reason,
            waiting_since=iso(now),
            waiting_history=history[-WAITING_HISTORY_LIMIT:],
            updated_at=iso(now),
        ):
            occurrence.waiting_reason = reason
            logger.info("Occurrence %s waiting: %s", occurrence.id, reason)

    def _expire(self, occurrence, now: float) -> None:
        """Close a waiting occurrence at its expiry; nothing more is spoken.

        A delivered occurrence whose remaining repeats did not fit simply
        closes (it was not missed, and needs no fallback). Otherwise it is
        missed; a pending retry after a definitive failure keeps 'failed' as
        its fallback reason, everything else is 'expired_undelivered'.
        """
        values = {
            "status": "closed",
            "waiting_reason": None,
            "next_attempt_at": None,
            "next_attempt_purpose": None,
            "closed_at": iso(now),
            "updated_at": iso(now),
        }
        if occurrence.delivery == "delivered":
            reason = None
        else:
            reason = occurrence.waiting_reason or (
                "retry_not_started"
                if occurrence.delivery == "failed"
                else "expired_before_dispatch"
            )
            fallback = "failed" if occurrence.delivery == "failed" else "expired_undelivered"
            values.update(
                disposition="missed",
                disposition_reason=reason,
                fallback_reason=fallback,
                fallback_eligible_at=iso(now),
            )
        if self.ctrl.update_announcement_occurrence(
            occurrence.id, expected=("waiting",), snapshot={"due_at": occurrence.due_at}, **values
        ):
            self._next_probe.pop(occurrence.id, None)
            logger.info(
                "Occurrence %s expired delivery=%s reason=%s",
                occurrence.id,
                occurrence.delivery,
                reason,
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
            expected_due_at=occurrence.due_at,
            not_expired_at=iso(now),
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
        if not target.configured:
            self._fail_occurrence(occurrence, target, self._unconfigured_reason(target))
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
            # A group never falls back to separate member streams: it waits,
            # and expiry turns it into a visible missed-reminder fallback.
            self._set_waiting(occurrence, self._unavailable_reason(target), now)
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
        purpose = occurrence.next_attempt_purpose or "announcement"
        counts = (
            {"retries_started": occurrence.retries_started + 1}
            if purpose == "retry"
            else {"announcements_started": occurrence.announcements_started + 1}
        )
        attempt.update(
            purpose=purpose,
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
            expected_due_at=occurrence.due_at,
            not_expired_at=iso(intent),
            status="announcing",
            waiting_reason=None,
            current_attempt_id=attempt["id"],
            updated_at=iso(intent),
            **counts,
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
        if (
            occurrence
            and latest
            and occurrence.status == "announcing"
            and occurrence.current_attempt_id == attempt.id
        ):
            self._settle(occurrence, latest)

    def _settle(self, occurrence, attempt) -> None:
        """Apply an attempt's evidence: repeat, retry or close the occurrence.

        - A requested Stop (or Done/Skip/cancel recorded meanwhile) ends all
          further automatic speech and never makes a fallback.
        - After a delivered announcement the owner's configured repeats follow
          ``repeat_interval_minutes`` after the previous start, one at a time
          (no burst after a stalled loop), only while they can start before
          the original expiry.
        - A failure that certainly did not play may be retried automatically,
          bounded in number and within the original expiry. Uncertain outcomes
          are never retried (no unintended duplicate).
        - Once any announcement was delivered, a later failure closes the
          occurrence as delivered without a fallback.
        """
        now = self._clock()
        disposition = occurrence.disposition
        disposition_reason = occurrence.disposition_reason
        stop_requested = attempt.stop_requested_at is not None
        if attempt.status == "stopped":
            if stop_requested:
                delivery = "delivered" if attempt.playback_observed_at else "none"
            else:
                delivery = "unknown"
        else:
            delivery = _DELIVERY[attempt.status]
        if stop_requested:
            disposition = disposition or "stopped"
            disposition_reason = disposition_reason or "stop_requested"
        delivered_before = occurrence.delivery == "delivered"
        delivery_reason = attempt.reason
        if delivery == "delivered" and affected_members(attempt.member_evidence):
            # Group playback reached only some members. The group cannot replay
            # to one member without replaying the others, so this is never
            # retried or repeated: it closes fallback-eligible as partial
            # (unless an earlier announcement already reached every member).
            delivery, delivery_reason = "partial", MEMBER_UNAVAILABLE
        if delivered_before:
            delivery, delivery_reason = "delivered", occurrence.delivery_reason
        elif delivery == "none" and occurrence.delivery != "none":
            delivery, delivery_reason = occurrence.delivery, occurrence.delivery_reason

        if disposition is None:
            follow = self._follow_up(occurrence, attempt, delivery, now)
            if follow is not None:
                purpose, at = follow
                if self.ctrl.update_announcement_occurrence(
                    occurrence.id,
                    expected=("announcing",),
                    snapshot={
                        "current_attempt_id": attempt.id,
                        "disposition": occurrence.disposition,
                    },
                    status="waiting",
                    delivery=delivery,
                    delivery_reason=delivery_reason,
                    next_attempt_at=iso(at),
                    next_attempt_purpose=purpose,
                    waiting_reason=None,
                    updated_at=iso(now),
                ):
                    logger.info(
                        "Occurrence %s %s scheduled at %s (announcements=%s retries=%s)",
                        occurrence.id,
                        purpose,
                        iso(at),
                        occurrence.announcements_started,
                        occurrence.retries_started,
                    )
                return

        values = {
            "status": "closed",
            "delivery": delivery,
            "delivery_reason": delivery_reason,
            "disposition": disposition,
            "disposition_reason": disposition_reason,
            "next_attempt_at": None,
            "next_attempt_purpose": None,
            "closed_at": iso(now),
            "updated_at": iso(now),
        }
        fallback = _FALLBACK.get(delivery)
        if fallback and disposition is None:
            values.update(fallback_reason=fallback, fallback_eligible_at=iso(now))
        if self.ctrl.update_announcement_occurrence(
            occurrence.id,
            expected=("announcing",),
            snapshot={"current_attempt_id": attempt.id, "disposition": occurrence.disposition},
            **values,
        ):
            logger.info(
                "Occurrence %s closed delivery=%s disposition=%s fallback=%s",
                occurrence.id,
                delivery,
                disposition,
                values.get("fallback_reason"),
            )

    def _follow_up(self, occurrence, attempt, delivery: str, now: float):
        """The next automatic dispatch, as (purpose, epoch), or None to close."""
        expires = epoch(occurrence.expires_at)
        if attempt.status == "finished" and delivery == "delivered":
            if any(
                s["prior_disposition"] == "stopped"
                for s in self.ctrl.list_announcement_snoozes([occurrence.id])
            ):
                return None  # A later deliberate snooze does not undo Stop's repeat cancellation.
            if occurrence.announcements_started >= occurrence.max_announcements:
                return None
            if not occurrence.repeat_interval_minutes:
                return None
            started = epoch(attempt.intent_at or attempt.requested_at)
            at = max(started + occurrence.repeat_interval_minutes * 60, now)
            return ("repeat", at) if at < expires else None
        # Only a definitive failure (never partial or unknown evidence) is retried.
        if delivery == "failed" and attempt.status == "failed" and attempt.certain_not_played:
            if occurrence.retries_started >= self.config.max_automatic_retries:
                logger.info("Occurrence %s retry budget exhausted", occurrence.id)
                return None
            at = now + self.config.retry_delay_s
            if at >= expires:
                logger.info("Occurrence %s retry would not start before expiry", occurrence.id)
                return None
            return ("retry", at)
        return None
