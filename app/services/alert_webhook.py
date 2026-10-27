import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BATCH_LOCK = threading.Lock()
_BATCH: dict[str, dict[str, Any]] = {}
_BATCH_THREAD: threading.Thread | None = None
_LAST_FLUSH_TS: float | None = None


@dataclass(frozen=True)
class WebhookResult:
    """What is known about one webhook request.

    ``status``: ``accepted`` (2xx: Discord took the message, which says nothing
    about readership), ``rejected`` (HTTP error answer), ``rate_limited`` (429,
    with ``retry_after_s`` when given), ``failed`` (certainly never sent, e.g.
    connection refused), ``not_configured`` (no URL) or ``unknown`` (sent or
    possibly sent without an answer, e.g. a read timeout: resending could
    duplicate the message).
    """

    status: str
    http_status: int | None = None
    retry_after_s: float | None = None
    detail: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


def _retry_after(resp) -> float | None:
    try:
        body = resp.json()
    except ValueError:
        body = None
    value = body.get("retry_after") if isinstance(body, dict) else None
    if value is None:
        value = (resp.headers or {}).get("Retry-After")
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _certainly_not_sent(exc: Exception) -> bool:
    """True only when the request provably never left this host."""
    if isinstance(exc, requests.ConnectTimeout | requests.exceptions.InvalidURL):
        return True
    if isinstance(exc, requests.exceptions.MissingSchema | requests.exceptions.InvalidSchema):
        return True
    if isinstance(exc, requests.ConnectionError) and exc.args:
        from urllib3.exceptions import MaxRetryError, NewConnectionError

        inner = exc.args[0]
        reason = inner.reason if isinstance(inner, MaxRetryError) else inner
        return isinstance(reason, NewConnectionError)
    return False


def deliver_webhook(
    *,
    payload: dict[str, Any],
    webhook_url: str | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 10,
    post: Callable[..., Any] | None = None,
) -> WebhookResult:
    """POST ``payload`` to the webhook and classify the outcome.

    Logs never include the URL (it embeds the webhook token) or the payload.
    """
    url = (webhook_url or os.getenv("ALERT_WEBHOOK_URL") or "").strip()
    if not url:
        logger.debug("alert webhook not configured")
        return WebhookResult("not_configured")
    kwargs: dict[str, Any] = {"json": payload, "timeout": timeout}
    if params:
        kwargs["params"] = params
    try:
        resp = (post or requests.post)(url, **kwargs)
    except Exception as exc:
        kind = type(exc).__name__
        if _certainly_not_sent(exc):
            logger.warning("alert webhook not sent: %s", kind)
            return WebhookResult("failed", detail=kind)
        logger.warning("alert webhook outcome unknown: %s", kind)
        return WebhookResult("unknown", detail=kind)
    code = resp.status_code
    if code < 400:
        return WebhookResult("accepted", http_status=code)
    if code == 429:
        retry = _retry_after(resp)
        logger.warning("alert webhook rate limited retry_after=%s", retry)
        return WebhookResult("rate_limited", http_status=code, retry_after_s=retry)
    logger.warning("alert webhook error: %s %s", code, (resp.text or "")[:200])
    return WebhookResult("rejected", http_status=code)


def send_alert_webhook(
    *,
    message: str,
    title: str | None = None,
    webhook_url: str | None = None,
    username: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Boolean sender for existing alerts; payload (and mentions) unchanged."""
    content = message if title is None else f"**{title}**\n{message}"
    payload: dict[str, Any] = {"content": content}
    if username:
        payload["username"] = username
    if extra:
        payload.update(extra)
    return deliver_webhook(payload=payload, webhook_url=webhook_url).accepted


def record_alert(
    *,
    key: str,
    message: str,
    title: str | None = None,
    severity: str = "warning",
    immediate: bool = False,
    extra: dict[str, Any] | None = None,
) -> bool:
    if immediate or severity == "critical":
        return send_alert_webhook(
            title=title or key,
            message=message,
            extra=extra,
        )

    now = time.time()
    with _BATCH_LOCK:
        entry = _BATCH.get(key)
        if entry is None:
            _BATCH[key] = {
                "count": 1,
                "first_ts": now,
                "last_ts": now,
                "message": message,
                "title": title or key,
                "severity": severity,
            }
        else:
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["last_ts"] = now
            entry["message"] = message
            if title:
                entry["title"] = title
            entry["severity"] = severity
    return True


def flush_alerts(*, force: bool = False) -> bool:
    global _LAST_FLUSH_TS
    now = time.time()
    interval_s = int(os.getenv("ALERT_WEBHOOK_BATCH_SECONDS", "3600"))

    last_flush = _LAST_FLUSH_TS
    with _BATCH_LOCK:
        if not _BATCH:
            return False
        if not force and last_flush is not None and (now - last_flush) < interval_s:
            return False
        items = list(_BATCH.items())
        _BATCH.clear()

    total = sum(int(v.get("count", 0)) for _, v in items)
    lines = []
    for key, data in sorted(items, key=lambda x: x[0]):
        count = data.get("count")
        first_ts = data.get("first_ts")
        last_ts = data.get("last_ts")
        title = data.get("title", key)
        msg = data.get("message", "")
        lines.append(
            f"- {title} [{count}] "
            f"(first={time.strftime('%H:%M:%S', time.localtime(first_ts))} "
            f"last={time.strftime('%H:%M:%S', time.localtime(last_ts))}): {msg}"
        )

    content = "\n".join(lines[:20])
    if len(lines) > 20:
        content += f"\n... ({len(lines) - 20} more)"

    ok = send_alert_webhook(
        title=f"Alert batch ({len(items)} types, {total} total)",
        message=content,
    )
    _LAST_FLUSH_TS = now
    return ok


def start_alert_batcher(interval_s: int | None = None) -> None:
    global _BATCH_THREAD
    if _BATCH_THREAD is not None:
        return

    url = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()
    if not url:
        logger.debug("alert webhook not configured; batcher not started")
        return

    if interval_s is None:
        interval_s = int(os.getenv("ALERT_WEBHOOK_BATCH_SECONDS", "3600"))

    def _loop() -> None:
        while True:
            try:
                flush_alerts(force=True)
            except Exception:
                logger.exception("Failed to flush alert batch")
            time.sleep(max(60, int(interval_s)))

    _BATCH_THREAD = threading.Thread(target=_loop, daemon=True)
    _BATCH_THREAD.start()
