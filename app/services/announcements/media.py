"""Media preparation: one WAV of attention chime, ~0.5 s silence, then speech.

The chime is generated here (stdlib only) at the speech sample rate so the
whole announcement is a single asset whose order and spacing cannot drift on
the speaker. Prepared assets live in a cache whose eviction is independent of
delivery history: history rows keep only the cache key.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .content import PreparedContent

logger = logging.getLogger(__name__)

MEDIA_VERSION = 1
GAP_SECONDS = 0.5
# The loudest chime (100 %) peaks at -6 dBFS so it never exceeds typical speech.
CHIME_FULL_SCALE = 0.5


class MediaError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PreparedMedia:
    key: str
    path: Path
    duration_s: float
    voice: str | None
    content_type: str = "audio/wav"


def media_key(content: PreparedContent, *, engine: str, voice: str | None, chime: int) -> str:
    material = json.dumps(
        {
            "v": MEDIA_VERSION,
            "text": content.spoken_text,
            "language": content.language,
            "engine": engine,
            "voice": voice,
            "chime": chime,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def chime_samples(rate: int, chime_percent: int) -> list[int]:
    """Two soft rising sine notes with smooth attack and release."""
    peak = 32767 * CHIME_FULL_SCALE * max(0, min(100, chime_percent)) / 100
    samples: list[int] = []
    for freq, seconds in ((659.25, 0.32), (880.0, 0.42)):
        count = int(seconds * rate)
        attack = int(0.03 * rate)
        for i in range(count):
            if i < attack:
                env = 0.5 - 0.5 * math.cos(math.pi * i / attack)
            else:
                # Raised-cosine release to exactly zero at the note end.
                env = 0.5 + 0.5 * math.cos(math.pi * (i - attack) / (count - attack))
            samples.append(round(peak * env * math.sin(2 * math.pi * freq * i / rate)))
    return samples


def compose(speech_wav: bytes, chime_percent: int) -> tuple[bytes, float]:
    try:
        with wave.open(io.BytesIO(speech_wav), "rb") as speech:
            if speech.getnchannels() != 1 or speech.getsampwidth() != 2:
                raise MediaError("unsupported_speech_format")
            rate = speech.getframerate()
            speech_frames = speech.readframes(speech.getnframes())
    except (wave.Error, EOFError) as exc:
        raise MediaError("invalid_speech_audio") from exc
    if not speech_frames:
        raise MediaError("empty_speech_audio")
    chime = chime_samples(rate, chime_percent)
    gap = int(GAP_SECONDS * rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{len(chime)}h", *chime))
        wav.writeframes(b"\x00\x00" * gap)
        wav.writeframes(speech_frames)
    total = len(chime) + gap + len(speech_frames) // 2
    return out.getvalue(), total / rate


class MediaCache:
    """Prepared audio files keyed by content; evicted by age and total size."""

    def __init__(self, directory: Path | str, *, clock: Callable[[], float], max_age_s, max_bytes):
        self.directory = Path(directory)
        self._clock = clock
        self.max_age_s = max_age_s
        self.max_bytes = max_bytes

    def path(self, key: str) -> Path:
        if not key or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("invalid media key")
        return self.directory / f"{key}.wav"

    def get(self, key: str) -> Path | None:
        path = self.path(key)
        if not path.exists():
            return None
        now = self._clock()
        try:
            os.utime(path, (now, now))
        except FileNotFoundError:
            return None  # concurrent eviction; a cache miss is safe
        return path

    def put(self, key: str, data: bytes) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        now = self._clock()
        os.utime(path, (now, now))
        return path

    def stats(self) -> dict:
        files = list(self.directory.glob("*.wav")) if self.directory.exists() else []
        sizes = []
        for path in files:
            try:
                sizes.append(path.stat().st_size)
            except FileNotFoundError:
                continue  # requests may read statistics during eviction
        return {"entries": len(sizes), "bytes": sum(sizes)}

    def evict(self, protected: set[str]) -> int:
        """Remove stale or excess entries, never those in active use."""
        if not self.directory.exists():
            return 0
        now = self._clock()
        entries = sorted(
            ((f.stat().st_mtime, f.stat().st_size, f) for f in self.directory.glob("*.wav")),
            key=lambda e: e[0],
        )
        total = sum(size for _m, size, _f in entries)
        removed = 0
        for mtime, size, path in entries:
            if path.stem in protected:
                continue
            if now - mtime > self.max_age_s or total > self.max_bytes:
                path.unlink(missing_ok=True)
                total -= size
                removed += 1
        if removed:
            logger.info("Announcement media cache evicted %s entries", removed)
        return removed
