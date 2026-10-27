"""Real Cast adapter with an external SDK/receiver double; no network."""

import sys
from types import SimpleNamespace
from uuid import UUID

from app.services.announcements import PyChromecastTransport, SpeakerTarget


class Receiver:
    def __init__(self):
        self.uuid = UUID("00000000-0000-0000-0000-000000000001")
        self.cast_info = SimpleNamespace(cast_type="audio", host="192.0.2.1", port=8009)
        self.status = SimpleNamespace(app_id="CC1AD845", volume_level=0.3)
        self.media_controller = self
        self.media = SimpleNamespace(content_id=None, player_state="IDLE", idle_reason=None)
        self.media_listeners = []
        self.cast_listeners = []
        self.stops = 0

    def wait(self, **kwargs):
        pass

    def register_status_listener(self, listener):
        # Cast and media controllers have the same test receiver; distinguish
        # the external SDK callbacks supported by each listener.
        self.media_listeners.append(listener)
        self.cast_listeners.append(listener)

    def register_connection_listener(self, listener):
        pass

    def play_media(self, url, *args, **kwargs):
        self.media = SimpleNamespace(content_id=url, player_state="PLAYING", idle_reason=None)
        for listener in self.media_listeners:
            listener.new_media_status(self.media)

    def stop(self, **kwargs):
        self.stops += 1
        self.media.player_state, self.media.idle_reason = "IDLE", "CANCELLED"
        for listener in self.media_listeners:
            listener.new_media_status(self.media)

    def disconnect(self, **kwargs):
        pass


class MediaController:
    def __init__(self, receiver):
        self.receiver = receiver

    @property
    def status(self):
        return self.receiver.media

    def __getattr__(self, key):
        return getattr(self.receiver, key)


def adapter(monkeypatch):
    receiver = Receiver()
    receiver.media_controller = MediaController(receiver)
    sdk = SimpleNamespace(
        get_listed_chromecasts=lambda **kwargs: ([receiver], None),
        discovery=SimpleNamespace(stop_discovery=lambda browser: None),
    )
    monkeypatch.setitem(sys.modules, "pychromecast", sdk)
    target = SpeakerTarget("right", "Right", "192.0.2.1", device_id=str(receiver.uuid))
    transport = PyChromecastTransport()
    transport.play(
        "attempt",
        target,
        "http://media.test/one.wav",
        content_type="audio/wav",
        title="Announcement",
    )
    return transport, target, receiver


def test_stop_does_not_stop_newer_owner_content(monkeypatch):
    transport, target, receiver = adapter(monkeypatch)
    receiver.media.content_id = "http://owner.test/new.wav"
    for listener in receiver.media_listeners:
        listener.new_media_status(receiver.media)
    assert transport.owns("attempt", target) is False
    assert transport.stop("attempt", target) == "unknown"
    assert receiver.stops == 0


def test_volume_changed_and_changed_back_never_reclaims_volume_ownership(monkeypatch):
    transport, target, receiver = adapter(monkeypatch)
    for volume in (0.65, 0.3):
        receiver.status.volume_level = volume
        for listener in receiver.cast_listeners:
            listener.new_cast_status(receiver.status)
    assert transport.owns("attempt", target) is True
    assert transport.owns("attempt", target, volume=True) is False
    assert transport.stop("attempt", target) == "stopped"


def test_buffering_or_missing_content_identity_is_not_playback_evidence(monkeypatch):
    transport, target, receiver = adapter(monkeypatch)
    # New attempt whose SDK load has not yet received its content report.
    receiver.play_media = lambda *args, **kwargs: None
    transport.play(
        "new", target, "http://media.test/new.wav", content_type="audio/wav", title="Announcement"
    )
    receiver.media.content_id = "http://media.test/new.wav"
    receiver.media.player_state = "BUFFERING"
    for listener in receiver.media_listeners:
        listener.new_media_status(receiver.media)
    assert transport.poll("new", target).state == "loading"
    receiver.media.content_id = None
    receiver.media.player_state = "PLAYING"
    for listener in receiver.media_listeners:
        listener.new_media_status(receiver.media)
    assert transport.poll("new", target).state != "playing"
    assert transport.owns("new", target) is False
