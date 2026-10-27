"""Scoped, unguessable, expiring playback capabilities for Cast receivers.

A speaker cannot send the owner's session cookie, so each dispatch gets a
random token naming exactly one prepared asset until it expires or the attempt
ends. Only a hash of the token is held; the token itself is never logged.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

MEDIA_PATH_PREFIX = "/announcement-media/"


@dataclass
class _Grant:
    media_key: str
    attempt_id: str
    expires_at: float


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class MediaGrants:
    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._grants: dict[str, _Grant] = {}
        self._lock = threading.Lock()

    def issue(self, media_key: str, attempt_id: str, ttl_s: float) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._grants[_digest(token)] = _Grant(media_key, attempt_id, self._clock() + ttl_s)
        return token

    def resolve(self, token: str) -> str | None:
        if not token or len(token) > 128:
            return None
        now = self._clock()
        with self._lock:
            grant = self._grants.get(_digest(token))
            if grant is None:
                return None
            if now >= grant.expires_at:
                del self._grants[_digest(token)]
                return None
            return grant.media_key

    def revoke_attempt(self, attempt_id: str) -> None:
        with self._lock:
            for digest in [d for d, g in self._grants.items() if g.attempt_id == attempt_id]:
                del self._grants[digest]

    def active_media_keys(self) -> set[str]:
        now = self._clock()
        with self._lock:
            return {g.media_key for g in self._grants.values() if now < g.expires_at}


def media_path(token: str) -> str:
    return f"{MEDIA_PATH_PREFIX}{token}.wav"
