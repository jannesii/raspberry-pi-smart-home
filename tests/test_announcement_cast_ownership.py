"""Real Cast adapter with an external SDK/receiver double; no network."""

import sys
from types import SimpleNamespace
from uuid import UUID

import announcement_support as support
import pytest

from app.services.announcements import PyChromecastTransport, SpeakerTarget, TransportError

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles
discord = support.discord


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


class ForeignAppReceiver(Receiver):
    """Replays a real JBL Right capture while the Spotify receiver owned it.

    The SDK's media controller does not require its own app, so a load sent
    while Spotify runs goes to Spotify, which ends it IDLE/INTERRUPTED without
    fetching the media. Launching the Default Media Receiver first plays it.
    """

    def __init__(self):
        super().__init__()
        self.status.app_id = "CC32E753"
        self.launched = []

    def start_app(self, app_id, **kwargs):
        self.launched.append(app_id)
        self.status.app_id = app_id
        for listener in self.cast_listeners:
            listener.new_cast_status(self.status)

    def play_media(self, url, *args, **kwargs):
        if self.status.app_id != "CC1AD845":
            self.media = SimpleNamespace(
                content_id=url, player_state="IDLE", idle_reason="INTERRUPTED"
            )
            for listener in self.media_listeners:
                listener.new_media_status(self.media)
            return
        super().play_media(url, *args, **kwargs)


def test_play_launches_default_receiver_when_another_app_owns_the_speaker(monkeypatch):
    receiver = ForeignAppReceiver()
    receiver.media_controller = MediaController(receiver)
    sdk = SimpleNamespace(
        get_listed_chromecasts=lambda **kwargs: ([receiver], None),
        discovery=SimpleNamespace(stop_discovery=lambda browser: None),
    )
    monkeypatch.setitem(sys.modules, "pychromecast", sdk)
    target = SpeakerTarget("right", "Right", "192.0.2.1", device_id=str(receiver.uuid))
    transport = PyChromecastTransport()
    transport.play(
        "urgent", target, "http://media.test/one.wav", content_type="audio/wav", title="Urgent"
    )
    assert receiver.launched == ["CC1AD845"]
    assert transport.poll("urgent", target).state == "playing"
    assert transport.owns("urgent", target, volume=True) is True


def test_failed_receiver_launch_is_certainly_not_played(monkeypatch):
    receiver = ForeignAppReceiver()
    receiver.media_controller = MediaController(receiver)

    def refuse(app_id, **kwargs):
        raise RuntimeError("launch refused")

    receiver.start_app = refuse
    sdk = SimpleNamespace(
        get_listed_chromecasts=lambda **kwargs: ([receiver], None),
        discovery=SimpleNamespace(stop_discovery=lambda browser: None),
    )
    monkeypatch.setitem(sys.modules, "pychromecast", sdk)
    target = SpeakerTarget("right", "Right", "192.0.2.1", device_id=str(receiver.uuid))
    transport = PyChromecastTransport()
    with pytest.raises(TransportError) as failure:
        transport.play(
            "urgent", target, "http://media.test/one.wav", content_type="audio/wav", title="Urgent"
        )
    assert (failure.value.reason, failure.value.certain_not_played) == ("launch_failed", True)
    assert transport.poll("urgent", target).state == "lost"


class FreshMediaController:
    """A newly connected SDK knows receiver status before media status."""

    def __init__(self, receiver):
        self.receiver = receiver
        self.listeners = []
        self.snapshot = SimpleNamespace(content_id=None, player_state="UNKNOWN", idle_reason=None)

    @property
    def status(self):
        return self.snapshot

    def register_status_listener(self, listener):
        self.listeners.append(listener)

    def publish(self):
        self.snapshot = SimpleNamespace(**vars(self.receiver.device.media))
        for listener in self.listeners:
            listener.new_media_status(self.snapshot)

    def update_status(self, *, callback_function=None):
        if self.receiver.device.media_reply == "unanswered":
            return
        if self.receiver.device.media_reply == "volume_changed_back":
            self.receiver.set_volume(0.45)
            self.receiver.set_volume(0.15)
        media = self.receiver.device.media
        # Captured JBL behavior: an idle Default Media Receiver answers with
        # MEDIA_STATUS/status=[]; the SDK leaves its fresh snapshot UNKNOWN.
        statuses = [] if media.player_state == "IDLE" else [{"playerState": media.player_state}]
        if statuses:
            self.publish()
        if callback_function:
            callback_function(True, {"type": "MEDIA_STATUS", "status": statuses})

    def play_media(self, *args, **kwargs):
        device = self.receiver.device
        device.status.app_id = "CC1AD845"
        device.media = SimpleNamespace(content_id=args[0], player_state="PLAYING", idle_reason=None)
        device.publish()


class FreshStatusConnection:
    def __init__(self, device):
        self.device = device
        self.uuid = device.uuid
        self.status = device.status
        self.cast_info = SimpleNamespace(cast_type="audio", host="192.168.1.119", port=8009)
        self.media_controller = FreshMediaController(self)
        self.cast_listeners = []
        self.closed = False

    @property
    def is_idle(self):
        return self.status.app_id is None

    def wait(self, **kwargs):
        pass

    def register_status_listener(self, listener):
        self.cast_listeners.append(listener)

    def register_connection_listener(self, listener):
        pass

    def set_volume(self, volume, **kwargs):
        self.device.status.volume_level = volume
        for connection in self.device.connections:
            if not connection.closed:
                for listener in connection.cast_listeners:
                    listener.new_cast_status(connection.status)

    def disconnect(self, **kwargs):
        self.closed = True


class FreshStatusReceiver:
    def __init__(self):
        self.uuid = UUID("00000000-0000-0000-0000-000000000001")
        self.status = SimpleNamespace(app_id=None, volume_level=0.3)
        self.media = SimpleNamespace(content_id=None, player_state="IDLE", idle_reason=None)
        self.connections = []
        self.media_reply = "empty"

    def connect(self, **kwargs):
        connection = FreshStatusConnection(self)
        self.connections.append(connection)
        return [connection], None

    def publish(self):
        for connection in self.connections:
            if not connection.closed and connection.media_controller.listeners:
                connection.media_controller.publish()

    def finish(self):
        self.media = SimpleNamespace(
            content_id=self.media.content_id, player_state="IDLE", idle_reason="FINISHED"
        )
        self.publish()


@pytest.mark.parametrize(
    ("media_reply", "restore", "final_volume"),
    [
        ("empty", "restored", 0.3),
        ("unanswered", "unknown", 0.15),
        ("volume_changed_back", "skipped_newer_intent", 0.15),
    ],
)
def test_finished_speaker_test_restores_volume_after_fresh_connection(
    announcement_app, client, monkeypatch, media_reply, restore, final_volume
):
    receiver = FreshStatusReceiver()
    receiver.media_reply = media_reply
    sdk = SimpleNamespace(
        get_listed_chromecasts=receiver.connect,
        discovery=SimpleNamespace(stop_discovery=lambda browser: None),
    )
    monkeypatch.setitem(sys.modules, "pychromecast", sdk)
    service = announcement_app.announcement_service
    service.transport = PyChromecastTransport(media_status_timeout_s=0.01)
    response = client.post(
        "/api/announcements/tests",
        json={
            "text": "Test volume restoration.",
            "target": "jbl-right",
            "volume_percent": 15,
            "chime_percent": 40,
            "request_id": "fresh-media-status-volume",
        },
        headers=support.headers(announcement_app, client),
    )
    assert response.status_code == 202
    identity = response.json["test"]["id"]
    service.tick()
    service.tick()
    assert receiver.status.volume_level == 0.15
    receiver.finish()
    attempt = client.get(f"/api/announcements/tests/{identity}").json["attempt"]
    assert service.transport.owns(attempt["id"], service.config.target("jbl-right"), volume=True)
    service.tick()
    attempt = client.get(f"/api/announcements/tests/{identity}").json["attempt"]
    assert attempt["status"] == "finished"
    assert attempt["volume_restore"] == restore
    assert receiver.status.volume_level == final_volume
