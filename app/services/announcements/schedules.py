"""Schedule rules: Helsinki wall time, instances, quiet hours and expiry.

Pure functions with no storage or Cast access so they can be tested through
the service and reused by later schedule kinds. The service only ever asks
"what is the next instance after this time?" (:func:`next_instance`).

Recurring rules are ``{"weekdays": [1..7 ISO, Monday=1], "times": ["HH:MM"]}``
in Helsinki wall time. Clock changes follow Helsinki:

- a time skipped by the spring change (e.g. 03:30 on the last Sunday of
  March) is still an instance, flagged as a DST gap, so it can be recorded as
  missed rather than silently dropped. Its instant is the wall time read with
  the pre-change offset (03:30+02:00), i.e. the same instant as 04:30 summer
  time; if the rule also has that real time, only the real one is kept;
- a time repeated by the autumn change is one instance, at its first
  (summer-time) occurrence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .content import AnnouncementInputError

HELSINKI = ZoneInfo("Europe/Helsinki")
DEFAULT_EXPIRY_MINUTES = 15
MAX_EXPIRY_MINUTES = 240
DEFAULT_QUIET_HOURS = "22:00-07:00"
_LOCAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_QUIET_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
_CLOCK_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MAX_RECURRING_TIMES = 12
MAX_ANNOUNCEMENTS = 5
MAX_REPEAT_INTERVAL_MINUTES = 120


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


def _whole(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and value == int(value)


def parse_repeats(count, interval) -> tuple[int, int | None]:
    """Validate the bounded repeat policy: (max announcements, interval minutes).

    One announcement is the default and needs no interval; more need one.
    """
    if count is None or count == "":
        count = 1
    if not _whole(count) or not 1 <= int(count) <= MAX_ANNOUNCEMENTS:
        raise AnnouncementInputError(
            "max_announcements",
            f"Announcements per reminder must be a whole number 1-{MAX_ANNOUNCEMENTS}.",
        )
    count = int(count)
    if interval is None or interval == "":
        if count > 1:
            raise AnnouncementInputError(
                "repeat_interval_minutes", "Choose a repeat interval in minutes for the repeats."
            )
        return count, None
    if count == 1:
        raise AnnouncementInputError(
            "repeat_interval_minutes",
            "A repeat interval needs more than one announcement per reminder.",
        )
    if not _whole(interval) or not 1 <= int(interval) <= MAX_REPEAT_INTERVAL_MINUTES:
        raise AnnouncementInputError(
            "repeat_interval_minutes",
            f"Repeat interval must be 1-{MAX_REPEAT_INTERVAL_MINUTES} whole minutes.",
        )
    return count, int(interval)


def repeat_fit(count: int, interval: int | None, expiry_minutes: int) -> dict:
    """Which configured announcements can start before expiry if the first is on time.

    Announcement *k* is due ``k * interval`` minutes after the due time and must
    start before expiry; later ones are never spoken. Delays (music, quiet hours,
    a busy speaker) push repeats later, so this is the best case.
    """
    offsets = [0]
    for k in range(1, count):
        if interval is None or k * interval >= expiry_minutes:
            break
        offsets.append(k * interval)
    return {
        "max_announcements": count,
        "repeat_interval_minutes": interval,
        "fit": len(offsets),
        "offsets_minutes": offsets,
    }


def parse_flag(value, field: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise AnnouncementInputError(field, f"{field} must be true or false.")
    return value


def parse_recurrence(value) -> dict:
    """Validate and normalise a recurring rule (sorted, without duplicates)."""
    if not isinstance(value, dict):
        raise AnnouncementInputError("recurrence", "Choose the days and times to repeat.")
    weekdays = value.get("weekdays")
    if (
        not isinstance(weekdays, list)
        or not weekdays
        or any(isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 7 for d in weekdays)
    ):
        raise AnnouncementInputError("weekdays", "Choose at least one weekday.")
    times = value.get("times")
    if not isinstance(times, list) or not times:
        raise AnnouncementInputError("times", "Add at least one time of day.")
    if any(not isinstance(t, str) or not _CLOCK_RE.match(t) for t in times):
        raise AnnouncementInputError("times", "Times must be HH:MM (00:00-23:59).")
    unique_times = sorted(set(times))
    if len(unique_times) > MAX_RECURRING_TIMES:
        raise AnnouncementInputError(
            "times", f"Use at most {MAX_RECURRING_TIMES} times of day per reminder."
        )
    return {"weekdays": sorted(set(weekdays)), "times": unique_times}


@dataclass(frozen=True)
class Instance:
    """One instance of a schedule: UTC epoch, Helsinki wall label, DST gap."""

    at: float
    local: str
    dst_gap: bool = False


def _day_instances(rule: dict, day: date) -> list[Instance]:
    if day.isoweekday() not in rule["weekdays"]:
        return []
    found: dict[float, Instance] = {}
    for text in rule["times"]:
        wall = datetime.combine(day, time.fromisoformat(text))
        first = wall.replace(tzinfo=HELSINKI, fold=0)  # autumn repeat: first only
        gap = first.astimezone(UTC).astimezone(HELSINKI).replace(tzinfo=None) != wall
        instance = Instance(first.timestamp(), wall.strftime("%Y-%m-%dT%H:%M"), gap)
        # A gap time shares its instant with a real time one hour later; the
        # real instance wins (see module docstring).
        if instance.at not in found or found[instance.at].dst_gap:
            found[instance.at] = instance
    return sorted(found.values(), key=lambda i: i.at)


def _recurring_after(rule: dict, after: float) -> Instance | None:
    start = datetime.fromtimestamp(after, HELSINKI).date() - timedelta(days=1)
    for offset in range(9):  # any non-empty weekly rule repeats within 8 days
        for instance in _day_instances(rule, start + timedelta(days=offset)):
            if instance.at > after:
                return instance
    return None


def instance_after(schedule, after: float) -> Instance | None:
    """The first instance strictly after ``after`` (epoch), or None.

    A one-time schedule has exactly one instance; once it has passed there is
    no next instance, so pausing across the due time or restarting late
    never revives it.
    """
    if schedule.kind == "one_time":
        if schedule.due_at is None:
            return None
        due = epoch(schedule.due_at)
        if due <= after:
            return None
        return Instance(due, helsinki_label(due))
    if schedule.kind == "recurring" and schedule.recurrence:
        return _recurring_after(schedule.recurrence, after)
    return None


def instance_at(schedule, at: float) -> Instance | None:
    """The instance of ``schedule`` at exactly ``at``, if the rule has one."""
    found = instance_after(schedule, at - 1)
    return found if found is not None and found.at == at else None


def next_instance(schedule, after: float) -> float | None:
    found = instance_after(schedule, after)
    return found.at if found is not None else None


def upcoming_instances(schedule, after: float, limit: int = 5) -> list[Instance]:
    found: list[Instance] = []
    cursor = after
    while len(found) < limit:
        nxt = instance_after(schedule, cursor)
        if nxt is None:
            break
        found.append(nxt)
        cursor = nxt.at
    return found


def helsinki_label(at: float, *, dst_gap: bool = False) -> str:
    """Helsinki wall time ``YYYY-MM-DDTHH:MM`` of an instance.

    A DST-gap instance is read with the offset in force just before the
    spring change (the gap is one hour), giving back the configured time.
    """
    if dst_gap:
        local = datetime.fromtimestamp(at - 3600, HELSINKI) + timedelta(hours=1)
    else:
        local = datetime.fromtimestamp(at, HELSINKI)
    return local.strftime("%Y-%m-%dT%H:%M")


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
