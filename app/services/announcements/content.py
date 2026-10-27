"""Announcement content preparation, separate from media and delivery.

Only owner-written static text exists today. A future dynamic provider would
implement :class:`ContentProvider` and return the same :class:`PreparedContent`;
media preparation and delivery never see where the words came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .config import DEFAULT_LANGUAGE, LANGUAGES


class AnnouncementInputError(ValueError):
    """Rejected owner input; ``field`` names the offending value."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class PreparedContent:
    text: str  # exactly what the owner wrote (kept for history)
    language: str
    source: str = "static_text"

    @property
    def spoken_text(self) -> str:
        """Whitespace collapsed so the synthesizer receives a single utterance."""
        return re.sub(r"\s+", " ", self.text).strip()


class ContentProvider(Protocol):
    def prepare(self, request: dict) -> PreparedContent: ...


class StaticTextProvider:
    """Validates owner-written text; never truncates or rewrites it."""

    def __init__(self, max_length: int = 500):
        self.max_length = max_length

    def prepare(self, request: dict) -> PreparedContent:
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AnnouncementInputError("text", "Write the announcement text.")
        text = text.strip()
        if len(text) > self.max_length:
            raise AnnouncementInputError(
                "text",
                f"Text is {len(text)} characters; the limit is {self.max_length}. "
                "Shorten it rather than relying on truncation.",
            )
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
            raise AnnouncementInputError("text", "Text contains control characters.")
        language = request.get("language") or DEFAULT_LANGUAGE
        if language not in LANGUAGES:
            raise AnnouncementInputError("language", "Language must be fi or en.")
        return PreparedContent(text=text, language=language)


def parse_percent(value, field: str, default: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise AnnouncementInputError(field, f"{field} must be a whole number 0-100.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AnnouncementInputError(field, f"{field} must be a whole number 0-100.") from exc
    if not number.is_integer() or not 0 <= number <= 100:
        raise AnnouncementInputError(field, f"{field} must be a whole number 0-100.")
    return int(number)
