"""Schedule rules: Helsinki wall time, instances, quiet hours and expiry.

Pure functions with no storage or Cast access so they can be tested through
the service and reused by later schedule kinds. Recurring rules (#34) extend
:func:`next_instance` / :func:`upcoming_instances` by ``kind``; the service
only ever asks "what is the next instance after this time?".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from .content import AnnouncementInputError

HELSINKI = ZoneInfo("Europe/Helsinki")
DEFAULT_EXPIRY_MINUTES = 15
MAX_EXPIRY_MINUTES = 240
DEFAULT_QUIET_HOURS = "22:00-07:00"
_LOCAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_QUIET_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def epoch(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def helsinki_epoch(local: object, fold: object = None) -> tuple[float, int | None]:
    """Resolve ``YYYY-MM-DDTHH:MM`` Helsinki wall time to a UTC epoch.

    Rejects times skipped by the spring clock change and requires ``fold``
    (0 = first, 1 = second) for times repeated in autumn. Returns the epoch and
    the fold that must be stored (``None`` when the time is unambiguous).
    """
    if not isinstance(local, str) or not _LOCAL_RE.match(local):
        raise AnnouncementInputError("due_local", "Choose a date and time (YYYY-MM-DDTHH:MM).")
    try:
        parsed = datetime.fromisoformat(local)
    except ValueError:
        raise AnnouncementInputError("due_local", "Choose a valid date and time.") from None
    if fold is not None and (isinstance(fold, bool) or fold not in (0, 1)):
        raise AnnouncementInputError("fold", "Choose the first or second occurrence.")
    choices = {}
    for occurrence in (0, 1):
        candidate = parsed.replace(tzinfo=HELSINKI, fold=occurrence)
        if candidate.astimezone(UTC).astimezone(HELSINKI).replace(tzinfo=None) == parsed:
            choices[candidate.timestamp()] = occurrence
    if not choices:
        raise AnnouncementInputError(
            "due_local",
            "This time does not exist in Europe/Helsinki (clocks move forward). "
            "Choose another time.",
        )
    if len(choices) > 1:
        if fold is None:
            raise AnnouncementInputError(
                "fold",
                "This time occurs twice in Europe/Helsinki (clocks move back). "
                "Choose the first or second occurrence.",
            )
        return parsed.replace(tzinfo=HELSINKI, fold=fold).timestamp(), fold
    return next(iter(choices)), None


def parse_expiry(value) -> int:
    if value is None or value == "":
        return DEFAULT_EXPIRY_MINUTES
    if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
        raise AnnouncementInputError("expiry_minutes", "Expiry must be whole minutes.")
    if not 1 <= int(value) <= MAX_EXPIRY_MINUTES:
        raise AnnouncementInputError(
            "expiry_minutes", f"Expiry must be 1-{MAX_EXPIRY_MINUTES} minutes."
        )
    return int(value)


def parse_flag(value, field: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise AnnouncementInputError(field, f"{field} must be true or false.")
    return value


def next_instance(schedule, after: float) -> float | None:
    """The first instance strictly after ``after`` (epoch), or None.

    Extension point for recurring kinds (#34). A one-time schedule has exactly
    one instance; once it has passed there is no next instance, so pausing
    across the due time or restarting late never revives it.
    """
    if schedule.kind == "one_time":
        if schedule.due_at is None:
            return None
        due = epoch(schedule.due_at)
        return due if due > after else None
    return None


def upcoming_instances(schedule, after: float, limit: int = 5) -> list[float]:
    found: list[float] = []
    cursor = after
    while len(found) < limit:
        nxt = next_instance(schedule, cursor)
        if nxt is None:
            break
        found.append(nxt)
        cursor = nxt
    return found


@dataclass(frozen=True)
class QuietHours:
    """A daily Helsinki interval, start inclusive and end exclusive."""

    start: time
    end: time

    @classmethod
    def parse(cls, raw: str | None) -> QuietHours | None:
        if not raw:
            return None
        match = _QUIET_RE.match(raw.strip())
        if not match:
            raise ValueError("Quiet hours must look like 22:00-07:00")
        h1, m1, h2, m2 = (int(g) for g in match.groups())
        if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59 or (h1, m1) == (h2, m2):
            raise ValueError("Quiet hours must be two different HH:MM times")
        return cls(time(h1, m1), time(h2, m2))

    def text(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"

    def contains(self, at: float) -> bool:
        local = datetime.fromtimestamp(at, HELSINKI).time()
        if self.start < self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end
