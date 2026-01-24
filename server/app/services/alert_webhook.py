import logging
import os
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BATCH_LOCK = threading.Lock()
_BATCH: dict[str, dict[str, Any]] = {}
_BATCH_THREAD: threading.Thread | None = None
_LAST_FLUSH_TS: float | None = None


def send_alert_webhook(
    *,
    message: str,
    title: str | None = None,
    webhook_url: str | None = None,
    username: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    url = (webhook_url or os.getenv("ALERT_WEBHOOK_URL") or "").strip()
    if not url:
        logger.debug("alert webhook not configured")
        return False

    content = message if title is None else f"**{title}**\n{message}"
    payload: dict[str, Any] = {"content": content}
    if username:
        payload["username"] = username
    if extra:
        payload.update(extra)

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code >= 400:
            logger.warning(
                "alert webhook error: %s %s", resp.status_code, resp.text
            )
            return False
        return True
    except Exception:
        logger.exception("Failed to send alert webhook")
        return False


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
        if not force and last_flush is not None:
            if (now - last_flush) < interval_s:
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
