"""Announcement targets and service settings (environment parsing lives here)."""

from __future__ import annotations

import json
import logging
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

LANGUAGES = ("fi", "en")
DEFAULT_LANGUAGE = "fi"
# Starting points only: perceived loudness must be calibrated by owner listening.
DEFAULT_VOLUME_PERCENT = 30
DEFAULT_CHIME_PERCENT = 40


@dataclass(frozen=True)
class SpeakerTarget:
    """An announcement target and the physical speakers it plays on.

    ``host``/``name`` are locators; ``device_id`` (the Cast UUID) is the stable
    identity when known. ``members`` lists physical speaker keys so a group
    target overlaps each member for queue serialization.

    A group (``kind='group'``) is the owner's existing Google Home group: its
    ``host`` is the group leader's address, ``port`` the group's own Cast port
    and ``device_id`` the group UUID (all read from discovery; nothing here
    creates or changes a group). It plays one synchronized stream; there is
    no per-member substitute.
    """

    key: str
    name: str
    host: str | None
    port: int = 8009
    device_id: str | None = None
    kind: str = "speaker"
    members: tuple[str, ...] = ()

    @property
    def speaker_set(self) -> frozenset[str]:
        return frozenset(self.members or (self.key,))

    @property
    def is_group(self) -> bool:
        return self.kind == GROUP

    @property
    def configured(self) -> bool:
        # A group is only addressed by its UUID at its leader host: without
        # both, a lookup could reach the leader speaker alone and silently
        # replace the synchronized group with a single stream.
        if self.is_group:
            return bool(self.host and self.device_id)
        return bool(self.host)

    def public(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "device_id": self.device_id,
            "kind": self.kind,
            "configured": self.configured,
            "members": sorted(self.speaker_set),
        }


GROUP = "group"
KINDS = ("speaker", GROUP)


def default_targets() -> tuple[SpeakerTarget, ...]:
    return (
        SpeakerTarget(key="jbl-right", name="Jbl-Right", host="192.168.1.119"),
        SpeakerTarget(key="jbl-left", name="Jbl-Left", host="192.168.1.148"),
        # The owner's existing Google Home group: unconfigured until its leader
        # host, port and UUID are set in ANNOUNCEMENT_TARGETS (see discovery).
        SpeakerTarget(
            key="jbl-group",
            name="Both JBLs (group)",
            host=None,
            kind=GROUP,
            members=("jbl-right", "jbl-left"),
        ),
    )


@dataclass(frozen=True)
class AnnouncementConfig:
    media_origin: str | None
    # Browser-facing origin of this app (e.g. https://jannenkoti.com) used for
    # the authenticated reminder link in fallback notifications; None = no link.
    public_origin: str | None = None
    targets: tuple[SpeakerTarget, ...] = field(default_factory=default_targets)
    cache_dir: Path | str = "announcement-cache"
    cache_max_age_s: float = 7 * 24 * 3600
    cache_max_bytes: int = 64 * 1024 * 1024
    media_grant_ttl_s: float = 600.0
    queue_timeout_s: float = 120.0
    playback_start_timeout_s: float = 30.0
    playback_finish_grace_s: float = 30.0
    max_text_length: int = 500
    # Helsinki "HH:MM-HH:MM", start inclusive, end exclusive; None disables.
    quiet_hours: str | None = "22:00-07:00"
    # Ordinary speech needs this long without playing/unknown media observed.
    music_idle_debounce_s: float = 10.0
    # How often a waiting occurrence re-probes its speaker.
    wait_probe_interval_s: float = 5.0

    def target(self, key: str) -> SpeakerTarget | None:
        return next((t for t in self.targets if t.key == key), None)


def normalize_origin(raw: str | None, setting: str = "ANNOUNCEMENT_MEDIA_ORIGIN") -> str | None:
    """Accept only a bare http(s) origin; anything else counts as unconfigured."""
    if not raw:
        return None
    parts = urlsplit(raw.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        logger.warning("%s ignored: not an http(s) origin", setting)
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment or "@" in parts.netloc:
        logger.warning("%s ignored: must be scheme://host[:port] only", setting)
        return None
    return f"{parts.scheme}://{parts.netloc}"


def parse_targets(raw: str | None) -> tuple[SpeakerTarget, ...]:
    if not raw:
        return default_targets()
    items = json.loads(raw)
    if not isinstance(items, list) or not items:
        raise ValueError("ANNOUNCEMENT_TARGETS must be a non-empty JSON list")
    targets = []
    for item in items:
        key = str(item.get("key") or "").strip()
        name = str(item.get("name") or "").strip()
        if not key or not name:
            raise ValueError("Each announcement target needs key and name")
        targets.append(
            SpeakerTarget(
                key=key,
                name=name,
                host=item.get("host") or None,
                port=int(item.get("port") or 8009),
                device_id=item.get("device_id") or None,
                kind=item.get("kind") or "speaker",
                members=tuple(item.get("members") or ()),
            )
        )
    if len({t.key for t in targets}) != len(targets):
        raise ValueError("Announcement target keys must be unique")
    speakers = {t.key for t in targets if not t.is_group}
    for target in targets:
        if target.kind not in KINDS:
            raise ValueError(f"Announcement target {target.key}: kind must be speaker or group")
        if not target.is_group and target.members:
            raise ValueError(f"Announcement target {target.key}: only a group has members")
        if target.is_group and (
            len(set(target.members)) < 2 or not set(target.members) <= speakers
        ):
            raise ValueError(
                f"Announcement group {target.key}: members must be two or more configured speakers"
            )
    return tuple(targets)


def config_from_env(default_cache_dir: Path) -> AnnouncementConfig:
    return AnnouncementConfig(
        media_origin=normalize_origin(os.getenv("ANNOUNCEMENT_MEDIA_ORIGIN")),
        public_origin=normalize_origin(
            os.getenv("ANNOUNCEMENT_PUBLIC_ORIGIN"), "ANNOUNCEMENT_PUBLIC_ORIGIN"
        ),
        targets=parse_targets(os.getenv("ANNOUNCEMENT_TARGETS")),
        cache_dir=os.getenv("ANNOUNCEMENT_CACHE_DIR") or default_cache_dir,
        quiet_hours=_quiet_hours_from_env(),
    )


def _quiet_hours_from_env() -> str | None:
    from .schedules import DEFAULT_QUIET_HOURS, QuietHours

    raw = os.getenv("ANNOUNCEMENT_QUIET_HOURS")
    if raw is None or not raw.strip():
        return DEFAULT_QUIET_HOURS
    if raw.strip().lower() in ("off", "none"):
        return None
    QuietHours.parse(raw)  # raises ValueError -> service init fails visibly
    return raw.strip()


def piper_settings_from_env() -> dict:
    voice_dir = os.getenv("PIPER_VOICE_DIR")
    return {
        "command": shlex.split(os.getenv("PIPER_COMMAND") or "piper"),
        "voice_dir": Path(voice_dir) if voice_dir else None,
        "voices": {
            "fi": os.getenv("PIPER_VOICE_FI") or "fi_FI-harri-medium",
            "en": os.getenv("PIPER_VOICE_EN") or "en_GB-alba-medium",
        },
    }
