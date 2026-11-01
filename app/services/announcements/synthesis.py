"""Explicitly selected local Piper or Azure Speech synthesis.

Piper (piper-tts) is GPL-3.0-or-later, so it is never imported into this
MIT-licensed app: the owner installs it in its own environment and points
``PIPER_COMMAND`` at its executable. Text travels on stdin (not argv, which
other local users can read). Cloud speech requires explicit configuration;
providers never switch automatically on failure.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol
from xml.etree import ElementTree

import requests

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)


class SynthesisError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Synthesizer(Protocol):
    engine: str

    def status(self) -> dict: ...

    def voice_for(self, language: str) -> str | None: ...

    def synthesize(self, text: str, language: str) -> bytes:
        """Return mono 16-bit PCM WAV bytes or raise :class:`SynthesisError`."""
        ...


def build_synthesizer(*, engine: str | None = None) -> Synthesizer:
    """Keep local synthesis unless the owner explicitly selects Azure."""
    from .config import piper_settings_from_env

    engine = (
        (engine if engine is not None else os.getenv("ANNOUNCEMENT_SPEECH_ENGINE", "piper"))
        .strip()
        .lower()
    )
    if engine == "piper":
        return PiperSubprocessSynthesizer(**piper_settings_from_env())
    if engine != "azure":
        raise ValueError("ANNOUNCEMENT_SPEECH_ENGINE must be piper or azure")
    return AzureSpeechSynthesizer(
        key=os.getenv("AZURE_SPEECH_KEY") or "",
        region=os.getenv("AZURE_SPEECH_REGION") or "",
        voices={
            "fi": os.getenv("AZURE_SPEECH_VOICE_FI") or "fi-FI-NooraNeural",
            "en": "en-GB-SoniaNeural",
        },
    )


class AzureSpeechSynthesizer:
    """Azure REST boundary; F0 provisioning enforces the free resource quota.

    No SDK, automatic retries, paid-tier upgrades or alternate-provider fallback.
    The caller's persistent media cache avoids synthesis on repeated playback.
    """

    engine = "azure"
    ALLOWED_VOICES: ClassVar = {
        "fi": {"fi-FI-NooraNeural", "fi-FI-SelmaNeural"},
        "en": {"en-GB-SoniaNeural"},
    }

    def __init__(self, *, key: str, region: str, voices=None, request=None):
        self._key = key.strip()
        self.region = region.strip().lower()
        self.voices = dict(
            voices if voices is not None else {"fi": "fi-FI-NooraNeural", "en": "en-GB-SoniaNeural"}
        )
        self._request = request or requests.post

    def voice_for(self, language: str) -> str | None:
        return self.voices.get(language)

    def status(self) -> dict:
        reason = None
        if not self._key:
            reason = "azure_key_missing"
        elif not re.fullmatch(r"[a-z][a-z0-9]{1,40}", self.region):
            reason = "azure_region_invalid"
        elif not self.voices.get("fi") or any(
            voice not in self.ALLOWED_VOICES.get(lang, set()) for lang, voice in self.voices.items()
        ):
            reason = "azure_voice_invalid"
        return {
            "available": reason is None,
            "engine": self.engine,
            "reason": reason,
            "voices": {
                lang: {"voice": voice, "installed": reason is None}
                for lang, voice in self.voices.items()
            },
        }

    def synthesize(self, text: str, language: str) -> bytes:
        reason = self.status()["reason"]
        if reason:
            raise SynthesisError(reason)
        voice = self.voice_for(language)
        if voice is None:
            raise SynthesisError(f"{language}_voice_missing")
        locale = {"fi": "fi-FI", "en": "en-GB"}[language]
        root = ElementTree.Element(
            "speak",
            {"version": "1.0", "xmlns": "http://www.w3.org/2001/10/synthesis", "xml:lang": locale},
        )
        ElementTree.SubElement(root, "voice", {"name": voice}).text = text
        try:
            response = self._request(
                f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={
                    "Ocp-Apim-Subscription-Key": self._key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
                    "User-Agent": "Jannenkoti-Announcements",
                },
                data=ElementTree.tostring(root, encoding="utf-8"),
                timeout=(5, 30),
                allow_redirects=False,
            )
        except requests.Timeout:
            raise SynthesisError("synthesis_timeout") from None
        except requests.RequestException:
            raise SynthesisError("azure_unavailable") from None
        if response.status_code != 200:
            reason = {
                401: "azure_authentication_failed",
                403: "azure_access_denied",
                429: "azure_quota_or_rate_limited",
            }.get(response.status_code, "synthesis_failed")
            # Provider bodies/exceptions can contain reminder text or credentials.
            logger.warning("Azure speech rejected synthesis status=%s", response.status_code)
            raise SynthesisError(reason)
        audio = response.content
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav:
                frames = wav.getnframes()
                if (
                    wav.getnchannels() != 1
                    or wav.getsampwidth() != 2
                    or wav.getframerate() != 24000
                    or not frames
                    or len(wav.readframes(frames)) != frames * 2
                ):
                    raise SynthesisError("invalid_speech_audio")
        except (wave.Error, EOFError):
            raise SynthesisError("invalid_speech_audio") from None
        return audio


class PiperSubprocessSynthesizer:
    engine = "piper"

    def __init__(
        self,
        *,
        command: Sequence[str],
        voice_dir: Path | None,
        voices: dict[str, str],
        timeout_s: float = 60.0,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.command = list(command)
        self.voice_dir = voice_dir
        self.voices = dict(voices)
        self.timeout_s = timeout_s
        self._run = runner

    def voice_for(self, language: str) -> str | None:
        return self.voices.get(language)

    def _model(self, language: str) -> Path | None:
        voice = self.voice_for(language)
        if not voice or self.voice_dir is None:
            return None
        model = self.voice_dir / f"{voice}.onnx"
        config = self.voice_dir / f"{voice}.onnx.json"
        return model if model.is_file() and config.is_file() else None

    def _executable(self) -> bool:
        if not self.command:
            return False
        exe = self.command[0]
        return bool(shutil.which(exe)) or Path(exe).is_file()

    def status(self) -> dict:
        voices = {
            lang: {"voice": voice, "installed": self._model(lang) is not None}
            for lang, voice in self.voices.items()
        }
        reason = None
        if not self._executable():
            reason = "piper_not_installed"
        elif self.voice_dir is None:
            reason = "voice_dir_not_configured"
        elif not voices.get("fi", {}).get("installed"):
            reason = "finnish_voice_missing"
        return {
            "available": reason is None,
            "engine": self.engine,
            "reason": reason,
            "voices": voices,
        }

    def synthesize(self, text: str, language: str) -> bytes:
        if not self._executable():
            raise SynthesisError("piper_not_installed")
        model = self._model(language)
        if model is None:
            raise SynthesisError(f"{language}_voice_missing")
        with tempfile.TemporaryDirectory(prefix="announcement-tts-") as tmp:
            out = Path(tmp) / "speech.wav"
            try:
                result = self._run(
                    [*self.command, "-m", str(model), "-f", str(out)],
                    input=text.encode("utf-8"),
                    capture_output=True,
                    timeout=self.timeout_s,
                    check=False,
                )
            except OSError as exc:
                raise SynthesisError("piper_not_runnable") from exc
            except Exception as exc:
                # Under Eventlet, green and stdlib subprocess define distinct
                # TimeoutExpired classes; either one means the same here.
                if type(exc).__name__ != "TimeoutExpired":
                    raise
                raise SynthesisError("synthesis_timeout") from exc
            if result.returncode != 0:
                # stderr can echo input text: log only its size.
                logger.warning(
                    "Piper exited with %s (stderr %s bytes)",
                    result.returncode,
                    len(result.stderr or b""),
                )
                raise SynthesisError("synthesis_failed")
            if not out.is_file() or out.stat().st_size <= 44:
                raise SynthesisError("synthesis_produced_no_audio")
            return out.read_bytes()
