"""Spoken announcements: content, media, local synthesis, Cast delivery.

See ``service.AnnouncementService`` for the public processing interface.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import AnnouncementConfig, SpeakerTarget, config_from_env, default_targets
from .content import AnnouncementInputError, PreparedContent, StaticTextProvider
from .service import AnnouncementService, AnnouncementStateError
from .transport import (
    CastTransport,
    PlaybackObservation,
    PyChromecastTransport,
    SpeakerProbe,
    TransportError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AnnouncementConfig",
    "AnnouncementInputError",
    "AnnouncementService",
    "AnnouncementStateError",
    "CastTransport",
    "PlaybackObservation",
    "PreparedContent",
    "PyChromecastTransport",
    "SpeakerProbe",
    "SpeakerTarget",
    "StaticTextProvider",
    "TransportError",
    "build_announcement_service",
    "default_targets",
]


def build_announcement_service(ctrl, *, db_path: str | None) -> AnnouncementService:
    """Production wiring: Piper subprocess + PyChromecast, both optional."""
    from .config import piper_settings_from_env
    from .synthesis import PiperSubprocessSynthesizer

    base = Path(db_path).resolve().parent if db_path else Path.cwd()
    config = config_from_env(base / "announcement-cache")
    synthesizer = PiperSubprocessSynthesizer(**piper_settings_from_env())
    service = AnnouncementService(
        ctrl, synthesizer=synthesizer, transport=PyChromecastTransport(), config=config
    )
    status = service.status()
    logger.info(
        "Announcement service ready synthesizer=%s cast=%s media_origin_configured=%s targets=%s",
        status["synthesizer"]["reason"] or "available",
        status["cast"]["reason"] or "available",
        status["media_origin"]["configured"],
        len(config.targets),
    )
    return service
