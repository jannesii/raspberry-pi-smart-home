"""Security-related application setup (rate limiting, request filters, API key auth)."""

from __future__ import annotations

import logging
from functools import wraps

from flask import current_app, g, jsonify, request

from .extensions import limiter

logger = logging.getLogger(__name__)


def configure_rate_limiting(app) -> None:
    """Initialize Flask-Limiter and register a whitelist request filter.

    Whitelist supports:
      - Exact IP match, e.g. "127.0.0.1"
      - Wildcard suffix, e.g. "192.168.10.*"
      - CIDR ranges, e.g. "192.168.10.0/24"
    """
    limiter.init_app(app)

    try:
        from ipaddress import ip_address, ip_network
    except Exception:
        ip_address = ip_network = None  # type: ignore

    def _rate_limit_whitelist_filter() -> bool:
        try:
            addr_raw = request.headers.get("X-Forwarded-For", request.remote_addr)
            if not addr_raw:
                return False
            addr = str(addr_raw).split(",")[0].strip()
            wl = current_app.config.get("whitelist", []) or []
            for item in wl:
                s = str(item).strip()
                if not s:
                    continue
                if s.endswith(".*"):
                    if addr.startswith(s[:-1]):
                        return True
                    continue
                if "/" in s and ip_address and ip_network:
                    try:
                        if ip_address(addr) in ip_network(s, strict=False):
                            return True
                        continue
                    except Exception:
                        pass
                if addr == s:
                    return True
            return False
        except Exception:
            return False

    try:
        # type: ignore[attr-defined]
        limiter.request_filter(_rate_limit_whitelist_filter)
    except Exception:
        logger.exception("Failed to register rate limit whitelist filter")

    logger.info(
        "Rate limiting enabled (whitelist size: %d)", len(app.config.get("whitelist", []) or [])
    )


def apply_security_headers(app) -> None:
    """Attach security headers to all responses."""
    logger.debug("apply_security_headers called")

    @app.after_request
    def _add_security_headers(response):
        logger.debug(
            "apply_security_headers adding headers path=%s status=%s",
            request.path,
            response.status,
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com "
                "https://cdn.socket.io; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                "img-src 'self' data:; "
                "connect-src 'self' ws: wss:; "
                "font-src 'self' data: https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "frame-ancestors 'self'"
            ),
        )
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), fullscreen=(self)",
        )
        return response


def require_api_key(view_func):
    """Decorator to protect endpoints with an API key.

    Accepts token via:
      - Authorization: Bearer <token>
      - X-API-Key: <token>
      - api_key query parameter (for GET/testing)
    Uses Controller.verify_api_key_token and stores metadata in g.api_key on success.
    """

    @wraps(view_func)
    def _wrapped(*args, **kwargs):
        try:
            token = None
            auth = request.headers.get("Authorization")
            if auth and isinstance(auth, str) and auth.lower().startswith("bearer "):
                token = auth.split(" ", 1)[1].strip()
            if not token:
                token = request.headers.get("X-API-Key")
            if not token:
                token = request.args.get("api_key")
            if not token:
                return jsonify(
                    {
                        "ok": False,
                        "error": "missing_api_key",
                        "message": "Provide Bearer token or X-API-Key.",
                    }
                ), 401
            ctrl = getattr(current_app, "ctrl", None)
            if ctrl is None:
                return jsonify({"ok": False, "error": "server_not_ready"}), 503
            meta = ctrl.verify_api_key_token(token)
            if not meta:
                return jsonify({"ok": False, "error": "invalid_api_key"}), 401
            g.api_key = meta
            return view_func(*args, **kwargs)
        except Exception as e:
            logger.exception("API key auth error: %s", e)
            return jsonify({"ok": False, "error": "auth_error"}), 500

    return _wrapped
