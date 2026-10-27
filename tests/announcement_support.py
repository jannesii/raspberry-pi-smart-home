"""Isolated announcement app, controlled clock and external-boundary doubles.

Shared by the announcement test modules: import the fixtures by name
(``from announcement_support import announcement_app, client, ...``). Only the
synthesis subprocess and the Cast transport are replaced; the Controller,
database, service policy, media cache and HTTP routes are real.
"""

from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from flask import Blueprint, Flask
from flask_login import LoginManager, UserMixin
from flask_wtf.csrf import generate_csrf
from sqlalchemy import create_engine

from app.blueprints.announcement_media import announcement_media_bp
from app.blueprints.api.announcements_api import announcements_bp
from app.blueprints.auth import auth_bp
from app.blueprints.web import web_bp
from app.core import Controller
from app.core.schema import metadata
from app.extensions import csrf
from app.services.announcements import (
    AnnouncementConfig,
    AnnouncementService,
    PlaybackObservation,
    SpeakerProbe,
    TransportError,
    default_targets,
)
from app.services.announcements.synthesis import SynthesisError

START = datetime(2027, 1, 15, 12, tzinfo=UTC).timestamp()
MEDIA_ORIGIN = "http://speaker-media.test:8099"
SPEECH_RATE = 22050


def speech_wav(seconds: float = 1.0, rate: int = SPEECH_RATE, amplitude: int = 9000) -> bytes:
    """A recognisable square-ish 'speech' signal with no silent samples."""
    frames = bytearray()
    for i in range(int(seconds * rate)):
        frames += struct.pack("<h", amplitude if (i // 20) % 2 == 0 else -amplitude)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))
    return out.getvalue()


def read_samples(data: bytes) -> tuple[int, list[int]]:
    with wave.open(io.BytesIO(data), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        rate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    return rate, list(struct.unpack(f"<{len(raw) // 2}h", raw))


def segments(samples: list[int], rate: int) -> dict:
    """Locate chime, silent gap and speech in a prepared announcement."""
    first_silent = next(
        i for i, s in enumerate(samples) if i > 0 and s == 0 and _silent(samples, i)
    )
    speech_start = next(i for i in range(first_silent, len(samples)) if samples[i] != 0)
    chime = samples[:first_silent]
    return {
        "chime_s": first_silent / rate,
        "gap_s": (speech_start - first_silent) / rate,
        "chime_peak": max(abs(s) for s in chime),
        "speech": samples[speech_start:],
    }


def _silent(samples, i, window=200):
    return all(s == 0 for s in samples[i : i + window])


class FakeSynthesizer:
    """Stands in for the Piper subprocess: deterministic audio per language."""

    engine = "fake"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.failure: str | None = None
        self.voices = {"fi": "fi_FI-test-medium", "en": "en_GB-test-medium"}

    def status(self) -> dict:
        return {"available": True, "engine": self.engine, "reason": None, "voices": self.voices}

    def voice_for(self, language: str) -> str | None:
        return self.voices.get(language)

    def synthesize(self, text: str, language: str) -> bytes:
        self.calls.append((text, language))
        if self.failure:
            raise SynthesisError(self.failure)
        seconds = 1.0 if language == "fi" else 0.6
        return speech_wav(seconds)


class SimulatedCrash(BaseException):
    """Process death inside an external effect: not handled by service code."""


@dataclass
class FakeSpeaker:
    host: str
    device_id: str | None
    state: str = "idle"  # idle | playing | unknown | unavailable
    volume: float | None = 0.4
    media: dict = field(default_factory=dict)  # attempt_id -> observation state


class FakeCast:
    """Deterministic Cast boundary. Records effects; never touches a network."""

    engine = "fake"

    def __init__(self):
        self.speakers = {
            "192.168.1.119": FakeSpeaker("192.168.1.119", "uuid-right"),
            "192.168.1.148": FakeSpeaker("192.168.1.148", "uuid-left"),
        }
        self.effects: list[tuple] = []
        self.plays: list[dict] = []
        self.play_error: TransportError | None = None
        self.play_crash = False
        self.stop_result = "stopped"
        self.available = True

    def status(self) -> dict:
        if not self.available:
            return {"available": False, "engine": self.engine, "reason": "not_installed"}
        return {"available": True, "engine": self.engine, "reason": None}

    def probe(self, target) -> SpeakerProbe:
        speaker = self.speakers.get(target.host)
        if speaker is None or speaker.state == "unavailable":
            return SpeakerProbe(state="unavailable", volume=None, device_id=None)
        return SpeakerProbe(state=speaker.state, volume=speaker.volume, device_id=speaker.device_id)

    def set_volume(self, target, level: float) -> None:
        self.effects.append(("set_volume", target.key, round(level, 3)))
        self.speakers[target.host].volume = level

    def play(self, attempt_id, target, media_url, *, content_type, title) -> None:
        self.effects.append(("play", target.key, attempt_id))
        self.plays.append({"attempt_id": attempt_id, "target": target.key, "url": media_url})
        if self.play_crash:
            raise SimulatedCrash()
        if self.play_error:
            raise self.play_error
        speaker = self.speakers[target.host]
        speaker.media[attempt_id] = "loading"
        speaker.state = "playing"

    def poll(self, attempt_id, target) -> PlaybackObservation:
        state = self.speakers[target.host].media.get(attempt_id, "lost")
        return PlaybackObservation(state=state)

    def stop(self, attempt_id, target) -> str:
        self.effects.append(("stop", target.key, attempt_id))
        if self.stop_result == "stopped":
            self.finish(target.host, attempt_id, "cancelled")
        return self.stop_result

    def release(self, attempt_id) -> None:
        self.effects.append(("release", attempt_id))

    # Test controls -----------------------------------------------------------
    def report(self, host: str, attempt_id: str, state: str) -> None:
        self.speakers[host].media[attempt_id] = state
        if state in ("finished", "failed", "cancelled", "interrupted"):
            self.speakers[host].state = "idle"

    finish = report


@pytest.fixture
def clock():
    return [START]


@pytest.fixture
def doubles():
    return FakeSynthesizer(), FakeCast()


def build_service(ctrl, clock, doubles, cache_dir, **config):
    synth, cast = doubles
    settings = {
        "media_origin": MEDIA_ORIGIN,
        "targets": default_targets(),
        "cache_dir": cache_dir,
    }
    settings.update(config)
    return AnnouncementService(
        ctrl,
        clock=lambda: clock[0],
        synthesizer=synth,
        transport=cast,
        config=AnnouncementConfig(**settings),
    )


class _User(UserMixin):
    def __init__(self, username):
        self.id = username


@pytest.fixture
def announcement_app(tmp_path, monkeypatch, clock, doubles):
    from app.core.database import DatabaseManager

    monkeypatch.setattr(DatabaseManager, "_instance", None)
    app = Flask(__name__, template_folder="../app/templates", static_folder="../app/static")
    app.config.update(TESTING=True, SECRET_KEY="isolated-announcement-test")
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    engine = create_engine(f"sqlite:///{tmp_path / 'announcements.db'}")
    metadata.create_all(engine)
    ctrl._sa_engine = engine
    ctrl.register_user("owner", password="ownerpass", is_admin=True, is_root_admin=True)
    ctrl.register_user("admin", password="adminpass", is_admin=True, is_root_admin=False)
    app.ctrl = ctrl
    app.config["DB_PATH"] = str(tmp_path / "legacy.db")
    app.config["ANNOUNCEMENT_CACHE_DIR"] = str(tmp_path / "cache")
    login = LoginManager(app)
    login.user_loader(lambda uid: _User(uid))
    root = Blueprint("announcement_api_root", __name__, url_prefix="/api")
    root.register_blueprint(announcements_bp)
    app.register_blueprint(root)
    app.register_blueprint(announcement_media_bp)
    app.register_blueprint(web_bp)
    app.register_blueprint(auth_bp)
    csrf.init_app(app)
    app.announcement_service = build_service(ctrl, clock, doubles, tmp_path / "cache")
    yield app
    engine.dispose()
    ctrl.db.conn.close()


def login(client, username="owner"):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True
    return client


@pytest.fixture
def client(announcement_app):
    return login(announcement_app.test_client())


def headers(app, client):
    with app.test_request_context():
        token = generate_csrf()
        from flask import session

        raw = session["csrf_token"]
    with client.session_transaction() as session:
        session["csrf_token"] = raw
    return {"X-CSRFToken": token}
