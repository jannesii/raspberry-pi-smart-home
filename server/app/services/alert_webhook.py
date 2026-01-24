import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


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
