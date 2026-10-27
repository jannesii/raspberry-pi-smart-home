"""Fallback notifications: one Discord message per reminder occurrence.

An occurrence becomes eligible when ``_settle``/``_expire`` record a
``fallback_reason`` (failed, partial, unknown, expired_undelivered). ``tick()``
then opens exactly one fallback for it (unique per occurrence) with the
message composed once, and sends it through the configured webhook
(``ALERT_WEBHOOK_URL``) immediately, never through the hourly alert batcher.

Budget: at most ``MAX_ATTEMPTS`` requests per fallback, each persisted as
'sending' before the request. Confirmed failures (HTTP error, rate limit,
certainly-not-sent) are retried after ``RETRY_DELAYS_S`` or the server's
``retry_after``, whichever is longer. Uncertain acceptance (timeout after
sending, or a restart mid-request) is recorded 'unknown' and never resent: a
missed duplicate is preferred to a blind one, and no exactly-once delivery is
claimed. Acceptance means Discord took the message, not that it was read, and
is never a reminder acknowledgement.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4

from ..alert_webhook import WebhookResult, deliver_webhook
from .groups import affected_members
from .schedules import HELSINKI, iso

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAYS_S = (60.0, 300.0)  # before attempt 2 and attempt 3
DISCORD_CONTENT_LIMIT = 2000
REMINDER_PAGE = "/settings/announcements"
_CONFIRMED_FAILURES = ("rejected", "rate_limited", "failed")

_HEADINGS = {
    "failed": "Reminder not spoken",
    "partial": "Reminder only partly spoken",
    "unknown": "Reminder may not have been spoken",
    "expired_undelivered": "Missed reminder",
}
_REASONS = {
    "synthesis_failed": "speech generation failed",
    "synthesis_timeout": "speech generation took too long",
    "piper_not_installed": "the local speech engine is not installed",
    "fi_voice_missing": "the Finnish voice is missing",
    "en_voice_missing": "the English voice is missing",
    "media_origin_not_configured": "the speaker media address is not configured",
    "cast_unavailable": "Cast is not available on the server",
    "target_not_configured": "the speaker is no longer configured",
    "target_removed": "the speaker is no longer configured",
    "device_identity_mismatch": "a different Cast device answered",
    "speaker_unavailable": "the speaker did not respond",
    "interrupted_by_restart": "the server restarted during the announcement",
    "no_playback_evidence": "the speaker never reported playback",
    "no_finish_evidence": "the speaker never reported the end of playback",
    "connection_lost": "the connection to the speaker was lost",
    "load_uncertain": "the speaker may or may not have received the audio",
    "dispatch_error": "sending to the speaker failed unexpectedly",
    "playback_failed": "the speaker reported a playback error",
    "interrupted": "something else took over the speaker",
    "stopped_externally": "playback was stopped from another app",
    "group_not_configured": "the speaker group is not configured",
    "group_unavailable": "the speaker group did not respond",
    "member_unavailable": "a speaker in the group did not take part",
}
_WAITED = {
    "quiet_hours": "quiet hours to end",
    "music_playing": "music to stop",
    "media_unknown": "the speaker state to clear",
    "speaker_busy": "another announcement to finish",
    "speaker_unavailable": "the speaker to respond",
    "group_unavailable": "the speaker group to respond",
}


class DiscordFallbackChannel:
    """The fallback channel: the existing alert webhook, mentions disabled."""

    channel = "discord"

    def __init__(self, webhook_url: str | None, *, post: Callable | None = None):
        self._url = (webhook_url or "").strip() or None
        self._post = post

    @property
    def configured(self) -> bool:
        return self._url is not None

    def status(self) -> dict:
        return {
            "channel": self.channel,
            "available": self.configured,
            "reason": None if self.configured else "not_configured",
        }

    def send(self, content: str) -> WebhookResult:
        if not self.configured:
            return WebhookResult("not_configured")
        return deliver_webhook(
            # parse=[] disables @everyone/@here, role and user mentions, including
            # any typed into the reminder text.
            payload={"content": content, "allowed_mentions": {"parse": []}},
            webhook_url=self._url,
            params={"wait": "true"},  # 200 + message: acceptance, not just a 204
            post=self._post,
        )


def _local(value: str | None) -> str:
    if not value:
        return "—"
    local = datetime.fromisoformat(value).astimezone(HELSINKI)
    return f"{local.day}.{local.month}.{local.year} {local:%H:%M}"


def _why(occurrence, affected=()) -> str:
    if occurrence.fallback_reason == "expired_undelivered":
        waited = []
        for entry in occurrence.waiting_history:
            text = _WAITED.get(entry.get("reason"), entry.get("reason"))
            if text and text not in waited:
                waited.append(text)
        expired = f"Expired at {_local(occurrence.expires_at)} without being spoken"
        if waited:
            return f"{expired} while waiting for {', '.join(waited)}."
        return f"{expired}; it could not be sent in time (for example the server was down)."
    reason = occurrence.delivery_reason
    detail = _REASONS.get(reason, reason) if reason else "no reason was reported"
    if occurrence.fallback_reason == "unknown":
        return f"Delivery is uncertain: {detail}. The speaker may have played it."
    if occurrence.fallback_reason == "partial":
        if affected:
            detail = f"{', '.join(affected)} did not take part"
        return f"Only part of {occurrence.target_name} reported playback: {detail}."
    return f"Spoken delivery failed: {detail}."


def compose_message(occurrence, link: str | None, affected=()) -> str:
    """Same reminder text as the speech, with delivery context and a page link.

    ``affected``: names of group members known not to have taken part.
    """
    heading = _HEADINGS[occurrence.fallback_reason]
    lines = [
        f"**{heading}** · {occurrence.target_name} · due {_local(occurrence.due_at)} (Helsinki)",
        occurrence.text,
        _why(occurrence, affected),
        f"Open the reminder: {link}" if link else "Open Settings → Announcements in the home app.",
    ]
    return "\n".join(lines)


class FallbackMixin:
    """Fallback processing and reads for AnnouncementService."""

    def _reminder_link(self, occurrence_id: str) -> str | None:
        origin = self.config.public_origin
        if not origin:
            return None
        return f"{origin}{REMINDER_PAGE}?occurrence={quote(occurrence_id, safe='')}"

    def _affected_names(self, occurrence) -> list[str]:
        if occurrence.fallback_reason != "partial" or not occurrence.current_attempt_id:
            return []
        attempt = self.ctrl.get_announcement_attempt(occurrence.current_attempt_id)
        return [m["name"] for m in affected_members(attempt.member_evidence if attempt else None)]

    def _process_fallbacks(self) -> None:
        now = self._clock()
        for occurrence in self.ctrl.list_announcement_occurrences_without_fallback():
            self._open_fallback(occurrence, now)
        for fallback in self.ctrl.list_announcement_fallbacks(statuses=("pending",)):
            if fallback.next_attempt_at and iso(now) < fallback.next_attempt_at:
                continue
            self._send_fallback(fallback)

    def _open_fallback(self, occurrence, now: float) -> None:
        link = self._reminder_link(occurrence.id)
        row = {
            "id": str(uuid4()),
            "occurrence_id": occurrence.id,
            "channel": self.fallback_channel.channel,
            "reason": occurrence.fallback_reason,
            "message": compose_message(occurrence, link, self._affected_names(occurrence)),
            "link": link,
            "status": "pending",
            "status_reason": None,
            "attempts_made": 0,
            "max_attempts": MAX_ATTEMPTS,
            "next_attempt_at": iso(now),
            "created_at": iso(now),
            "updated_at": iso(now),
        }
        if len(row["message"]) > DISCORD_CONTENT_LIMIT:
            # Never truncate a reminder silently; make the problem visible.
            row.update(status="failed", status_reason="message_too_long", next_attempt_at=None)
            row["completed_at"] = iso(now)
        if self.ctrl.create_announcement_fallback(row):
            logger.info(
                "Fallback %s opened for occurrence %s reason=%s",
                row["id"],
                occurrence.id,
                occurrence.fallback_reason,
            )

    def _send_fallback(self, fallback) -> None:
        number = fallback.attempts_made + 1
        attempt_id = str(uuid4())
        if not self.ctrl.begin_announcement_fallback_attempt(
            fallback.id, {"id": attempt_id, "number": number, "intent_at": iso(self._clock())}
        ):
            return  # another tick took this attempt
        try:
            result = self.fallback_channel.send(fallback.message)
        except Exception:
            logger.exception("Fallback %s request raised unexpectedly", fallback.id)
            result = WebhookResult("unknown", detail="send_error")
        now = self._clock()
        values = self._fallback_outcome(fallback, number, result, now)
        self.ctrl.finish_announcement_fallback_attempt(
            fallback.id,
            attempt_id,
            {
                "status": result.status,
                "http_status": result.http_status,
                "retry_after_s": result.retry_after_s,
                "detail": result.detail,
                "finished_at": iso(now),
            },
            **values,
        )
        logger.info(
            "Fallback %s attempt %s/%s: %s -> %s",
            fallback.id,
            number,
            fallback.max_attempts,
            result.status,
            values["status"],
        )

    @staticmethod
    def _fallback_outcome(fallback, number: int, result: WebhookResult, now: float) -> dict:
        done = {"updated_at": iso(now), "completed_at": iso(now), "next_attempt_at": None}
        if result.status == "accepted":
            return {**done, "status": "accepted", "status_reason": None, "accepted_at": iso(now)}
        if result.status == "not_configured":
            return {**done, "status": "unavailable", "status_reason": "not_configured"}
        if result.status not in _CONFIRMED_FAILURES:
            return {**done, "status": "unknown", "status_reason": "acceptance_unknown"}
        permanent = (
            result.status == "rejected"
            and result.http_status is not None
            and 400 <= result.http_status < 500
            and result.http_status != 408
        )
        if permanent:
            return {**done, "status": "failed", "status_reason": "rejected"}
        if number >= fallback.max_attempts:
            return {**done, "status": "failed", "status_reason": "attempts_exhausted"}
        delay = max(RETRY_DELAYS_S[number - 1], result.retry_after_s or 0.0)
        return {
            "status": "pending",
            "status_reason": result.status,
            "next_attempt_at": iso(now + delay),
            "updated_at": iso(now),
        }

    def _recover_fallbacks(self, now: str) -> None:
        recovered = self.ctrl.recover_announcement_fallbacks(now)
        if recovered:
            logger.warning("Marked %s interrupted fallback requests unknown", recovered)

    # Reads ------------------------------------------------------------------------
    def _fallbacks_by_occurrence(self, occurrence_ids: list[str]) -> dict[str, dict]:
        if not occurrence_ids:
            return {}
        fallbacks = self.ctrl.list_announcement_fallbacks(occurrence_ids=occurrence_ids)
        attempts: dict[str, list] = {}
        for attempt in self.ctrl.list_announcement_fallback_attempts([f.id for f in fallbacks]):
            attempts.setdefault(attempt.fallback_id, []).append(asdict(attempt))
        return {
            f.occurrence_id: {**asdict(f), "attempts": attempts.get(f.id, [])} for f in fallbacks
        }

    def fallback_status(self) -> dict:
        latest = self.ctrl.list_announcement_fallbacks(newest_first=True, limit=1)
        last = None
        if latest:
            f = latest[0]
            last = {
                "status": f.status,
                "status_reason": f.status_reason,
                "reason": f.reason,
                "updated_at": f.updated_at,
                "occurrence_id": f.occurrence_id,
            }
        return {
            **self.fallback_channel.status(),
            "max_attempts": MAX_ATTEMPTS,
            "link_origin": self.config.public_origin,
            "last": last,
        }
