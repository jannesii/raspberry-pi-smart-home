"""Shared Discord webhook sender: richer delivery result, unchanged boolean callers.

Only the HTTP boundary (``requests.post``) is replaced; no webhook is contacted.
"""

from __future__ import annotations

import logging

import pytest
import requests

from app.services import alert_webhook
from app.services.alert_webhook import deliver_webhook, send_alert_webhook

HOOK = "https://discord.test/api/webhooks/123/secret-token"


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = "" if body is None else str(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakePost:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _connection_refused() -> requests.ConnectionError:
    from urllib3.exceptions import MaxRetryError, NewConnectionError

    reason = NewConnectionError(None, "Failed to establish a new connection: refused")
    return requests.ConnectionError(MaxRetryError(None, HOOK, reason))


@pytest.mark.parametrize(
    ("outcome", "status", "http_status", "retry_after"),
    [
        (FakeResponse(204), "accepted", 204, None),
        (FakeResponse(200, {"id": "m1"}), "accepted", 200, None),
        (FakeResponse(400, {"message": "Cannot send an empty message"}), "rejected", 400, None),
        (FakeResponse(503), "rejected", 503, None),
        (FakeResponse(429, {"retry_after": 2.5}), "rate_limited", 429, 2.5),
        (FakeResponse(429, None, {"Retry-After": "7"}), "rate_limited", 429, 7.0),
        (requests.ConnectTimeout("connect timed out"), "failed", None, None),
        (_connection_refused(), "failed", None, None),
        (requests.ReadTimeout("read timed out"), "unknown", None, None),
        (requests.ConnectionError("connection reset by peer"), "unknown", None, None),
    ],
)
def test_delivery_result_separates_acceptance_rejection_and_uncertainty(
    outcome, status, http_status, retry_after
):
    post = FakePost(outcome)
    result = deliver_webhook(payload={"content": "Hei"}, webhook_url=HOOK, post=post)
    assert result.status == status
    assert result.http_status == http_status
    assert result.retry_after_s == retry_after
    assert result.accepted is (status == "accepted")
    assert len(post.calls) == 1


def test_missing_configuration_is_its_own_outcome(monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    post = FakePost()
    result = deliver_webhook(payload={"content": "Hei"}, post=post)
    assert result.status == "not_configured"
    assert post.calls == []


def test_delivery_logs_never_contain_the_webhook_url(caplog):
    caplog.set_level(logging.DEBUG, logger=alert_webhook.__name__)
    deliver_webhook(
        payload={"content": "x"},
        webhook_url=HOOK,
        post=FakePost(requests.ConnectionError(f"Max retries exceeded with url: {HOOK}")),
    )
    deliver_webhook(payload={"content": "x"}, webhook_url=HOOK, post=FakePost(FakeResponse(404)))
    assert caplog.records
    assert "secret-token" not in caplog.text


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (FakeResponse(204), True),
        (FakeResponse(400), False),
        (FakeResponse(429, {"retry_after": 1}), False),
        (requests.ReadTimeout("read timed out"), False),
        (RuntimeError("anything else"), False),
    ],
)
def test_existing_boolean_sender_keeps_its_payload_and_result(monkeypatch, outcome, expected):
    """Medicine alerts rely on @everyone: old callers get no allowed_mentions."""
    post = FakePost(outcome)
    monkeypatch.setattr(alert_webhook.requests, "post", post)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", HOOK)
    ok = send_alert_webhook(title="Medicine Alert", message="@everyone take it", username="Bot")
    assert ok is expected
    assert post.calls == [
        {
            "url": HOOK,
            "json": {"content": "**Medicine Alert**\n@everyone take it", "username": "Bot"},
            "timeout": 10,
        }
    ]


def test_existing_boolean_sender_without_configuration_sends_nothing(monkeypatch):
    post = FakePost()
    monkeypatch.setattr(alert_webhook.requests, "post", post)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    assert send_alert_webhook(message="hello") is False
    assert post.calls == []


def test_rejected_response_cannot_echo_credentials_or_private_content_into_logs(caplog):
    caplog.set_level(logging.DEBUG, logger=alert_webhook.__name__)
    result = deliver_webhook(
        payload={"content": "PRIVATE_CANARY"},
        webhook_url=HOOK,
        post=FakePost(FakeResponse(400, "TOKEN_CANARY PRIVATE_CANARY")),
    )
    assert result.status == "rejected"
    assert "TOKEN_CANARY" not in caplog.text
    assert "PRIVATE_CANARY" not in caplog.text


@pytest.mark.parametrize("code", [101, 302])
def test_only_2xx_is_accepted_by_delivery_boundary_but_legacy_boolean_is_preserved(
    monkeypatch, code
):
    result = deliver_webhook(payload={}, webhook_url=HOOK, post=FakePost(FakeResponse(code)))
    assert result.status == "unknown"
    monkeypatch.setattr(alert_webhook.requests, "post", FakePost(FakeResponse(code)))
    assert send_alert_webhook(message="legacy", webhook_url=HOOK) is True
