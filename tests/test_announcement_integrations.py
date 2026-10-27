"""Local synthesis subprocess and optional Cast wiring, without real engines.

A stand-in executable plays Piper's role at the process boundary; nothing here
loads a voice model, contacts a speaker or reaches the network.
"""

from __future__ import annotations

import json
import logging
import sys
import textwrap

import pytest
from announcement_support import read_samples, segments

from app.services.announcements import PyChromecastTransport, build_announcement_service
from app.services.announcements.synthesis import PiperSubprocessSynthesizer, SynthesisError

FAKE_PIPER = textwrap.dedent(
    """
    import json, struct, sys, wave
    args = sys.argv[1:]
    text = sys.stdin.read()
    record = {"argv": args, "stdin": text}
    with open(__file__ + ".calls", "a") as log:
        log.write(json.dumps(record) + "\\n")
    if "FAIL" in text:
        sys.stderr.write("could not speak: " + text)
        sys.exit(3)
    if "SLOW" in text:
        import time; time.sleep(5)
    out = args[args.index("-f") + 1]
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(22050)
        wav.writeframes(struct.pack("<h", 4000) * 22050)
    """
)


@pytest.fixture
def piper(tmp_path):
    script = tmp_path / "fake_piper.py"
    script.write_text(FAKE_PIPER)
    voices = tmp_path / "voices"
    voices.mkdir()
    for voice in ("fi_FI-harri-medium", "en_GB-alba-medium"):
        (voices / f"{voice}.onnx").write_bytes(b"model")
        (voices / f"{voice}.onnx.json").write_text("{}")
    synth = PiperSubprocessSynthesizer(
        command=[sys.executable, str(script)],
        voice_dir=voices,
        voices={"fi": "fi_FI-harri-medium", "en": "en_GB-alba-medium"},
        timeout_s=1,
    )
    return synth, script, voices


def _calls(script):
    return [json.loads(line) for line in (script.parent / (script.name + ".calls")).open()]


def test_piper_gets_text_on_stdin_and_the_language_voice_model(piper):
    synth, script, voices = piper
    fi_audio = synth.synthesize("Muista ottaa lääkkeet.", "fi")
    synth.synthesize("Measure blood pressure.", "en")
    rate, samples = read_samples(fi_audio)
    assert rate == 22050 and len(samples) == 22050
    first, second = _calls(script)
    assert first["stdin"] == "Muista ottaa lääkkeet."
    assert "Muista ottaa lääkkeet." not in " ".join(first["argv"])  # argv is world-readable
    assert first["argv"][first["argv"].index("-m") + 1] == str(voices / "fi_FI-harri-medium.onnx")
    assert second["argv"][second["argv"].index("-m") + 1] == str(voices / "en_GB-alba-medium.onnx")
    assert synth.status() == {
        "available": True,
        "engine": "piper",
        "reason": None,
        "voices": {
            "fi": {"voice": "fi_FI-harri-medium", "installed": True},
            "en": {"voice": "en_GB-alba-medium", "installed": True},
        },
    }


def test_piper_failures_are_reported_without_echoing_the_text(piper, caplog):
    synth, _script, voices = piper
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SynthesisError) as failed:
        synth.synthesize("FAIL Salainen muistutus", "fi")
    assert failed.value.reason == "synthesis_failed"
    assert "Salainen" not in caplog.text
    with pytest.raises(SynthesisError) as slow:
        synth.synthesize("SLOW", "fi")
    assert slow.value.reason == "synthesis_timeout"
    (voices / "en_GB-alba-medium.onnx").unlink()
    with pytest.raises(SynthesisError) as missing:
        synth.synthesize("Hello", "en")
    assert missing.value.reason == "en_voice_missing"
    assert synth.status()["voices"]["en"]["installed"] is False


def test_missing_local_engine_is_unavailable_with_no_other_engine(tmp_path):
    absent = PiperSubprocessSynthesizer(
        command=[str(tmp_path / "no-such-piper")], voice_dir=tmp_path, voices={"fi": "fi_FI-x"}
    )
    assert absent.status()["reason"] == "piper_not_installed"
    with pytest.raises(SynthesisError) as missing:
        absent.synthesize("Hei", "fi")
    assert missing.value.reason == "piper_not_installed"
    unconfigured = PiperSubprocessSynthesizer(
        command=[sys.executable], voice_dir=None, voices={"fi": "fi_FI-x"}
    )
    assert unconfigured.status()["reason"] == "voice_dir_not_configured"
    no_finnish = PiperSubprocessSynthesizer(
        command=[sys.executable], voice_dir=tmp_path, voices={"fi": "fi_FI-x"}
    )
    assert no_finnish.status()["reason"] == "finnish_voice_missing"


def test_preview_through_the_piper_adapter_has_chime_gap_and_speech(piper, tmp_path, monkeypatch):
    from announcement_support import START, FakeCast, build_service
    from sqlalchemy import create_engine

    from app.core import Controller
    from app.core.database import DatabaseManager
    from app.core.schema import metadata

    synth, _script, _voices = piper
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    metadata.create_all(ctrl._sa_engine)
    service = build_service(ctrl, [START], (synth, FakeCast()), tmp_path / "cache")
    preview = service.request_preview(text="Hei   maailma\n", chime_percent=40)
    service.tick()
    assert service.get_preview(preview["id"])["status"] == "ready"
    rate, samples = read_samples(service.preview_audio(preview["id"]).read_bytes())
    parts = segments(samples, rate)
    assert abs(parts["gap_s"] - 0.5) < 0.02
    assert len(parts["speech"]) == 22050
    assert _calls(_script)[0]["stdin"] == "Hei maailma"  # one utterance, owner text kept
    ctrl._sa_engine.dispose()
    ctrl.db.conn.close()


def test_cast_library_absence_is_reported_not_raised():
    transport = PyChromecastTransport()
    status = transport.status()
    if status["available"]:
        pytest.skip("pychromecast is installed here; absence path not exercised")
    assert status["reason"] == "pychromecast_not_installed"
    from app.services.announcements import default_targets

    probe = transport.probe(default_targets()[0])
    assert probe.state == "unavailable"


def test_production_wiring_reports_defaults_and_unconfigured_parts(tmp_path, monkeypatch):
    from app.core import Controller
    from app.core.database import DatabaseManager

    for name in (
        "ANNOUNCEMENT_MEDIA_ORIGIN",
        "ANNOUNCEMENT_TARGETS",
        "ANNOUNCEMENT_CACHE_DIR",
        "PIPER_COMMAND",
        "PIPER_VOICE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PIPER_COMMAND", str(tmp_path / "missing-piper"))
    monkeypatch.setenv("ANNOUNCEMENT_MEDIA_ORIGIN", "http://192.168.1.10:8099/path")
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    from sqlalchemy import create_engine

    from app.core.schema import metadata

    ctrl._sa_engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    metadata.create_all(ctrl._sa_engine)
    service = build_announcement_service(ctrl, db_path=str(tmp_path / "legacy.db"))
    status = service.status()
    assert not service.running  # wiring does not start the loop by itself
    assert status["media_origin"]["configured"] is False  # a path is not an origin
    assert status["synthesizer"]["reason"] == "piper_not_installed"
    assert [(t["key"], t["name"], t["host"]) for t in status["targets"]] == [
        ("jbl-right", "Jbl-Right", "192.168.1.119"),
        ("jbl-left", "Jbl-Left", "192.168.1.148"),
    ]
    assert status["defaults"]["language"] == "fi"
    monkeypatch.setenv("ANNOUNCEMENT_MEDIA_ORIGIN", "http://192.168.1.10:8099")
    monkeypatch.setenv(
        "ANNOUNCEMENT_TARGETS",
        json.dumps(
            [
                {
                    "key": "jbl-right",
                    "name": "Jbl-Right",
                    "host": "192.168.1.119",
                    "device_id": "0f3c-uuid",
                }
            ]
        ),
    )
    configured = build_announcement_service(ctrl, db_path=str(tmp_path / "legacy.db")).status()
    assert configured["media_origin"] == {"configured": True, "origin": "http://192.168.1.10:8099"}
    assert configured["targets"][0]["device_id"] == "0f3c-uuid"
    ctrl._sa_engine.dispose()
    ctrl.db.conn.close()
