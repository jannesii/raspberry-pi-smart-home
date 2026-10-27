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
from typing import TYPE_CHECKING

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
    SpeakerTarget,
    TransportError,
    default_targets,
)
from app.services.announcements.fallback import DiscordFallbackChannel
from app.services.announcements.synthesis import SynthesisError

if TYPE_CHECKING:
    from collections.abc import Callable

START = datetime(2027, 1, 15, 12, tzinfo=UTC).timestamp()
MEDIA_ORIGIN = "http://speaker-media.test:8099"
PUBLIC_ORIGIN = "https://home.test"
WEBHOOK_URL = "https://discord.test/api/webhooks/42/webhook-secret"
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
        # Runs inside synthesis, i.e. while the service prepares a dispatch:
        # lets tests interleave owner actions, clock jumps or a crash there.
        self.during: Callable[[], None] | None = None

    def status(self) -> dict:
        return {"available": True, "engine": self.engine, "reason": None, "voices": self.voices}

    def voice_for(self, language: str) -> str | None:
        return self.voices.get(language)

    def synthesize(self, text: str, language: str) -> bytes:
        self.calls.append((text, language))
        if self.during:
            hook, self.during = self.during, None
            hook()
        if self.failure:
            raise SynthesisError(self.failure)
        seconds = 1.0 if language == "fi" else 0.6
        return speech_wav(seconds)


class SimulatedCrash(BaseException):
    """Process death inside an external effect: not handled by service code."""


GROUP_HOST = "192.168.1.119"  # the group's leader; its Cast port differs from 8009
GROUP_PORT = 32187
GROUP = f"{GROUP_HOST}:{GROUP_PORT}"  # FakeCast key of the group receiver


def group_targets(*, member_ids: bool = True) -> tuple[SpeakerTarget, ...]:
    """Both JBLs plus the owner's existing Google Home group, as configured."""
    return (
        SpeakerTarget(
            key="jbl-right",
            name="Jbl-Right",
            host="192.168.1.119",
            device_id="uuid-right" if member_ids else None,
        ),
        SpeakerTarget(
            key="jbl-left",
            name="Jbl-Left",
            host="192.168.1.148",
            device_id="uuid-left" if member_ids else None,
        ),
        SpeakerTarget(
            key="jbl-group",
            name="Both JBLs",
            host=GROUP_HOST,
            port=GROUP_PORT,
            device_id="uuid-group",
            kind="group",
            members=("jbl-right", "jbl-left"),
        ),
    )


@dataclass
class FakeSpeaker:
    host: str
    device_id: str | None
    state: str = "idle"  # idle | playing | unknown | unavailable
    volume: float | None = 0.4
    media: dict = field(default_factory=dict)  # attempt_id -> observation state
    name: str = "JBL Link Portable"
    port: int = 8009
    # Group receivers: keys of member receivers, and whether the group reports
    # its current membership (multizone status) at all.
    group_of: tuple[str, ...] = ()
    membership_reported: bool = True


class FakeCast:
    """Deterministic Cast boundary. Records effects; never touches a network."""

    engine = "fake"

    def __init__(self):
        self.speakers = {
            "192.168.1.119": FakeSpeaker("192.168.1.119", "uuid-right", name="Jbl-Right"),
            "192.168.1.148": FakeSpeaker("192.168.1.148", "uuid-left", name="Jbl-Left"),
            GROUP: FakeSpeaker(
                GROUP_HOST,
                "uuid-group",
                name="Koti kaiuttimet",
                port=GROUP_PORT,
                group_of=("192.168.1.119", "192.168.1.148"),
            ),
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

    @staticmethod
    def _key(target) -> str | None:
        if target.host is None:
            return None
        return target.host if target.port == 8009 else f"{target.host}:{target.port}"

    def _speaker(self, target) -> FakeSpeaker:
        return self.speakers[self._key(target)]

    def _member_ids(self, speaker: FakeSpeaker):
        """Members a group currently reports (those reachable), or None."""
        if not speaker.group_of or not speaker.membership_reported:
            return None
        members = [self.speakers[key] for key in speaker.group_of]
        return tuple(m.device_id for m in members if m.state != "unavailable")

    def probe(self, target) -> SpeakerProbe:
        speaker = self.speakers.get(self._key(target))
        if speaker is None or speaker.state == "unavailable":
            return SpeakerProbe(state="unavailable", volume=None, device_id=None)
        return SpeakerProbe(
            state=speaker.state,
            volume=speaker.volume,
            device_id=speaker.device_id,
            member_ids=self._member_ids(speaker),
        )

    def discover(self, hosts) -> list[dict]:
        self.effects.append(("discover", tuple(hosts)))
        return [
            {
                "device_id": s.device_id,
                "name": s.name,
                "model": "Google Cast Group" if s.group_of else "JBL Link Portable",
                "cast_type": "group" if s.group_of else "audio",
                "host": s.host,
                "port": s.port,
            }
            for s in self.speakers.values()
            if s.host in hosts and s.state != "unavailable"
        ]

    def set_volume(self, target, level: float) -> None:
        self.effects.append(("set_volume", target.key, round(level, 3)))
        self._speaker(target).volume = level

    def play(self, attempt_id, target, media_url, *, content_type, title) -> None:
        self.effects.append(("play", target.key, attempt_id))
        self.plays.append({"attempt_id": attempt_id, "target": target.key, "url": media_url})
        if self.play_crash:
            raise SimulatedCrash()
        if self.play_error:
            raise self.play_error
        speaker = self._speaker(target)
        speaker.media[attempt_id] = "loading"
        speaker.state = "playing"
        for key in speaker.group_of:  # group playback occupies its reachable members
            if self.speakers[key].state != "unavailable":
                self.speakers[key].state = "playing"

    def poll(self, attempt_id, target) -> PlaybackObservation:
        speaker = self._speaker(target)
        state = speaker.media.get(attempt_id, "lost")
        return PlaybackObservation(state=state, member_ids=self._member_ids(speaker))

    def stop(self, attempt_id, target) -> str:
        self.effects.append(("stop", target.key, attempt_id))
        if self.stop_result == "stopped":
            self.finish(self._key(target), attempt_id, "cancelled")
        return self.stop_result

    def release(self, attempt_id) -> None:
        self.effects.append(("release", attempt_id))

    # Test controls -----------------------------------------------------------
    def report(self, host: str, attempt_id: str, state: str) -> None:
        """``host`` is a FakeCast key: a speaker host or ``GROUP``."""
        speaker = self.speakers[host]
        speaker.media[attempt_id] = state
        if state in ("finished", "failed", "cancelled", "interrupted"):
            speaker.state = "idle"
            for key in speaker.group_of:
                if self.speakers[key].state == "playing":
                    self.speakers[key].state = "idle"

    finish = report


class DiscordResponse:
    def __init__(self, status_code: int, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = "" if body is None else str(body)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeDiscord:
    """Stands in for Discord's HTTP endpoint (``requests.post``); no network.

    ``outcomes`` is consumed per request (a response or an exception to
    raise); when empty every request is accepted with 200 + message JSON.
    """

    def __init__(self):
        self.outcomes: list = []
        self.requests: list[dict] = []
        self.during: Callable[[], None] | None = None

    def post(self, url, **kwargs):
        self.requests.append({"url": url, **kwargs})
        if self.during:
            hook, self.during = self.during, None
            hook()
        outcome = self.outcomes.pop(0) if self.outcomes else DiscordResponse(200, {"id": "m"})
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def messages(self) -> list[str]:
        return [r["json"]["content"] for r in self.requests]


@pytest.fixture
def discord():
    return FakeDiscord()


@pytest.fixture
def clock():
    return [START]


@pytest.fixture
def doubles():
    return FakeSynthesizer(), FakeCast()


def build_service(
    ctrl, clock, doubles, cache_dir, *, discord=None, webhook_url=WEBHOOK_URL, **config
):
    """Without ``discord`` the fallback channel is unconfigured (never sends)."""
    synth, cast = doubles
    settings = {
        "media_origin": MEDIA_ORIGIN,
        "public_origin": PUBLIC_ORIGIN,
        "targets": default_targets(),
        "cache_dir": cache_dir,
    }
    settings.update(config)
    channel = (
        DiscordFallbackChannel(webhook_url, post=discord.post)
        if discord is not None
        else DiscordFallbackChannel(None)
    )
    return AnnouncementService(
        ctrl,
        clock=lambda: clock[0],
        synthesizer=synth,
        transport=cast,
        config=AnnouncementConfig(**settings),
        fallback_channel=channel,
    )


class _User(UserMixin):
    def __init__(self, username):
        self.id = username


@pytest.fixture
def announcement_app(tmp_path, monkeypatch, clock, doubles, discord):
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
    app.announcement_service = build_service(
        ctrl, clock, doubles, tmp_path / "cache", discord=discord
    )
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
