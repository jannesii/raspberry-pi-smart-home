"""Shared FMI WFS plumbing for the weather archive collectors.

Namespaces, the malformed-reply error, one fetch that records its outcome on a
retrieval, and the aligned-slot scheduler each collector thread runs on.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import requests

from .weather_service import FMI_WFS_URL

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "om": "http://www.opengis.net/om/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "wml2": "http://www.opengis.net/waterml/2.0",
    "target": "http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1",
}
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


class MalformedReply(ValueError):
    """The reply is not a parsable WFS observation collection."""


def fetch_reply(http, params, timeout_s, parse, retrieval: dict, now) -> tuple:
    """Fetch and parse one FMI reply, recording the outcome on ``retrieval``.

    Returns ``(content, parsed)`` for a successful reply and ``(None, None)``
    otherwise; failed or malformed replies keep no content.
    """
    content = parsed = None
    try:
        response = http.get(FMI_WFS_URL, params=params, timeout=timeout_s)
        retrieval["http_status"] = response.status_code
        response.raise_for_status()
        content = response.content or b""
        parsed = parse(content)
        retrieval["status"] = "ok"
    except requests.Timeout:
        retrieval.update(status="failed", error="timeout")
    except requests.HTTPError:
        retrieval.update(status="failed", error="http_error")
    except requests.RequestException as exc:
        retrieval.update(status="failed", error=f"request_failed:{type(exc).__name__}")
    except MalformedReply:
        retrieval.update(status="failed", error="malformed_reply")
    retrieval["retrieved_at"] = now().isoformat()
    if retrieval["status"] != "ok":
        return None, None
    return content, parsed


class AlignedCollector:
    """Run ``collect`` once per aligned interval slot on a server-owned thread."""

    thread_name = "WeatherCollector"

    def __init__(self, *, clock: Callable[[], float], interval_s: int) -> None:
        self._clock = clock
        self.interval_s = int(interval_s)
        self._last_slot: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.archive_errors: dict[str, dict] = {}

    def collect(self, slot: datetime) -> None:
        raise NotImplementedError

    def run_pending(self) -> bool:
        """Collect once per aligned slot. Returns True when collected."""
        now = self._clock()
        slot = now - (now % self.interval_s)
        if self._last_slot is not None and slot <= self._last_slot:
            return False
        self._last_slot = slot
        self.collect(datetime.fromtimestamp(slot, UTC))
        return True

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self.thread_name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def now(self) -> datetime:
        """Current time on the collector's clock (controllable in tests)."""
        return datetime.fromtimestamp(self._clock(), UTC)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_pending()
            except Exception:
                logger.exception("%s cycle failed", self.thread_name)
            now = self._clock()
            wait = self.interval_s - (now % self.interval_s)
            self._stop.wait(max(1.0, min(wait, self.interval_s)))
