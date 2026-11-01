"""Azure is replaced only at HTTP; synthesis, cache and provider policy are real."""

import logging
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
import requests
from announcement_support import START, FakeCast, build_service, speech_wav

from app.services.announcements.synthesis import SynthesisError


def azure(**kwargs):
    from app.services.announcements.synthesis import AzureSpeechSynthesizer

    return AzureSpeechSynthesizer(key="synthetic-secret", region="northeurope", **kwargs)


def test_azure_posts_escaped_finnish_ssml_and_returns_pcm():
    calls = []
    audio = speech_wav(rate=24000)

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, content=audio)

    synth = azure(request=post)
    assert synth.synthesize('Muista <vesi> & "lepo".', "fi") == audio
    url, call = calls[0]
    assert url == "https://northeurope.tts.speech.microsoft.com/cognitiveservices/v1"
    assert call["headers"]["Ocp-Apim-Subscription-Key"] == "synthetic-secret"
    assert call["headers"]["X-Microsoft-OutputFormat"] == "riff-24khz-16bit-mono-pcm"
    assert call["timeout"] == (5, 30)
    assert call["allow_redirects"] is False
    root = ElementTree.fromstring(call["data"])
    voice = next(iter(root))
    assert voice.attrib["name"] == "fi-FI-NooraNeural"
    assert voice.text == 'Muista <vesi> & "lepo".'
    assert synth.status()["available"] is True
    assert "synthetic-secret" not in str(synth.status())


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "azure_authentication_failed"),
        (403, "azure_access_denied"),
        (429, "azure_quota_or_rate_limited"),
        (500, "synthesis_failed"),
        (302, "synthesis_failed"),
    ],
)
def test_azure_rejections_do_not_retry_or_log_provider_body(status, reason, caplog):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=status, content=b"synthetic-secret private text")

    with caplog.at_level(logging.DEBUG), pytest.raises(SynthesisError) as error:
        azure(request=post).synthesize("private text", "fi")
    assert error.value.reason == reason
    assert len(calls) == 1
    assert "synthetic-secret" not in caplog.text
    assert "private text" not in caplog.text


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (requests.Timeout("private text"), "synthesis_timeout"),
        (requests.ConnectionError("synthetic-secret"), "azure_unavailable"),
    ],
)
def test_azure_network_failures_are_safe(failure, reason):
    def post(*args, **kwargs):
        raise failure

    with pytest.raises(SynthesisError) as error:
        azure(request=post).synthesize("private text", "fi")
    assert error.value.reason == reason
    assert str(error.value) == reason


@pytest.mark.parametrize("audio", [b"", b"not a wave file", speech_wav(rate=24000)[:-10]])
def test_azure_rejects_invalid_or_truncated_audio(audio):
    synth = azure(request=lambda *a, **kw: SimpleNamespace(status_code=200, content=audio))
    with pytest.raises(SynthesisError) as error:
        synth.synthesize("Test", "fi")
    assert error.value.reason == "invalid_speech_audio"


def test_provider_configuration_defaults_and_validation(monkeypatch):
    from app.services.announcements.synthesis import build_synthesizer

    monkeypatch.delenv("ANNOUNCEMENT_SPEECH_ENGINE", raising=False)
    assert build_synthesizer().engine == "piper"
    monkeypatch.setenv("ANNOUNCEMENT_SPEECH_ENGINE", "azure")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "northeurope")
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    assert build_synthesizer().status()["reason"] == "azure_key_missing"
    monkeypatch.setenv("AZURE_SPEECH_KEY", "synthetic-secret")
    assert build_synthesizer().voice_for("fi") == "fi-FI-NooraNeural"
    monkeypatch.setenv("AZURE_SPEECH_VOICE_FI", "fi-FI-SelmaNeural")
    assert build_synthesizer().voice_for("fi") == "fi-FI-SelmaNeural"
    monkeypatch.setenv("AZURE_SPEECH_REGION", "evil.example/path")
    assert build_synthesizer().status()["reason"] == "azure_region_invalid"
    with pytest.raises(SynthesisError, match="azure_region_invalid"):
        build_synthesizer().synthesize("Test", "fi")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "northeurope")
    monkeypatch.setenv("AZURE_SPEECH_VOICE_FI", "en-US-paid-HD")
    assert build_synthesizer().status()["reason"] == "azure_voice_invalid"
    monkeypatch.setenv("ANNOUNCEMENT_SPEECH_ENGINE", "typo")
    with pytest.raises(ValueError, match="ANNOUNCEMENT_SPEECH_ENGINE"):
        build_synthesizer()


def test_azure_preview_reuses_cache_across_services_and_invalidates_on_voice_change(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine

    from app.core import Controller
    from app.core.database import DatabaseManager
    from app.core.schema import metadata

    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["data"])
        return SimpleNamespace(status_code=200, content=speech_wav(rate=24000))

    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    metadata.create_all(ctrl._sa_engine)
    try:
        for voice in ("fi-FI-NooraNeural", "fi-FI-NooraNeural", "fi-FI-SelmaNeural"):
            synth = azure(request=post, voices={"fi": voice, "en": "en-GB-SoniaNeural"})
            service = build_service(ctrl, [START], (synth, FakeCast()), tmp_path / "cache")
            preview = service.request_preview(text="Tämä on testi.", chime_percent=40)
            service.tick()
            assert service.get_preview(preview["id"])["status"] == "ready"
        assert len(calls) == 2  # restart reuses the file; new voice requires new speech
    finally:
        ctrl._sa_engine.dispose()
        ctrl.db.conn.close()


def test_development_requires_explicit_cloud_speech_selection(monkeypatch):
    from app.development_acceptance import selection, speech_synthesizer, summary

    monkeypatch.setenv("ANNOUNCEMENT_SPEECH_ENGINE", "azure")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "synthetic-secret")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "northeurope")
    assert speech_synthesizer({}).engine == "piper"
    values = {
        "DEV_ACCEPTANCE": '{"speakers":[{"key":"test","name":"Test","host":"127.0.0.1"}],"speech":"azure"}',
        "DEV_ACCEPTANCE_MEDIA_ORIGIN": "http://127.0.0.1:8099",
    }
    chosen = selection(values)
    assert speech_synthesizer(chosen).engine == "azure"
    assert "Azure Speech" in summary(values)
    values["DEV_ACCEPTANCE"] = '{"speech":"typo"}'
    with pytest.raises(ValueError, match="speech"):
        selection(values)
