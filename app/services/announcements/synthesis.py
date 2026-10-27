"""Local speech synthesis through a configured Piper subprocess.

Piper (piper-tts) is GPL-3.0-or-later, so it is never imported into this
MIT-licensed app: the owner installs it in its own environment and points
``PIPER_COMMAND`` at its executable. Text travels on stdin (not argv, which
other local users can read). There is deliberately no cloud fallback.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

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
