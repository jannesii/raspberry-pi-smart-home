"""Explicit announcement acceptance; no credentials imply permission.

The announcement implementation remains on its own branch. This adapter uses
its public constructor when both features are combined; no announcement code
or production targets are duplicated here.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


class AcceptanceConfigError(ValueError):
    pass


def selection(values=None):
    values = os.environ if values is None else values
    try:
        data = json.loads(values.get("DEV_ACCEPTANCE") or "{}")
    except (ValueError, TypeError):
        raise AcceptanceConfigError("DEV_ACCEPTANCE must be a JSON object") from None
    if not isinstance(data, dict):
        raise AcceptanceConfigError("DEV_ACCEPTANCE must be a JSON object")
    if set(data) - {"speakers", "discord"}:
        raise AcceptanceConfigError(
            "DEV_ACCEPTANCE contains an unknown integration; Spotify is unsupported"
        )
    speakers = data.get("speakers", [])
    if not isinstance(speakers, list):
        raise AcceptanceConfigError("DEV_ACCEPTANCE speakers must be a list")
    keys = set()
    for target in speakers:
        if not isinstance(target, dict) or set(target) - {
            "key",
            "name",
            "host",
            "port",
            "device_id",
        }:
            raise AcceptanceConfigError(
                "DEV_ACCEPTANCE speaker fields are invalid; groups are unsupported"
            )
        if not isinstance(target.get("key"), str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{1,64}", target["key"]
        ):
            raise AcceptanceConfigError("DEV_ACCEPTANCE speaker key is invalid")
        if target["key"] in keys:
            raise AcceptanceConfigError("DEV_ACCEPTANCE speaker keys must be unique")
        keys.add(target["key"])
        if not isinstance(target.get("name"), str) or not re.fullmatch(
            r"[\w .()-]{1,64}", target["name"]
        ):
            raise AcceptanceConfigError("DEV_ACCEPTANCE speaker name is invalid")
        if not isinstance(target.get("host"), str):
            raise AcceptanceConfigError("DEV_ACCEPTANCE speaker host must be a literal address")
        try:
            host = ipaddress.ip_address(target.get("host", ""))
            port = int(target.get("port", 8009))
        except (ValueError, TypeError):
            raise AcceptanceConfigError(
                "DEV_ACCEPTANCE needs literal speaker addresses and valid ports"
            ) from None
        if not (host.is_private or host.is_loopback) or host.is_unspecified or host.is_multicast:
            raise AcceptanceConfigError("DEV_ACCEPTANCE speaker must have a LAN address")
        if not 1 <= port <= 65535:
            raise AcceptanceConfigError("DEV_ACCEPTANCE speaker port is invalid")
        target["port"] = port
        target["host"] = str(host)
    discord = data.get("discord")
    if discord is not None:
        if not isinstance(discord, dict) or set(discord) != {"label", "webhook"}:
            raise AcceptanceConfigError("DEV_ACCEPTANCE discord needs label and webhook")
        if not isinstance(discord.get("label"), str) or not re.fullmatch(
            r"[\w .()-]{1,64}", discord["label"]
        ):
            raise AcceptanceConfigError("DEV_ACCEPTANCE discord label is invalid")
        if not isinstance(discord.get("webhook"), str):
            raise AcceptanceConfigError("DEV_ACCEPTANCE discord webhook must be text")
        try:
            parsed = urlsplit(discord.get("webhook", ""))
        except ValueError:
            raise AcceptanceConfigError("DEV_ACCEPTANCE discord webhook is invalid") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"discord.com", "discordapp.com"}
            or not parsed.path.startswith("/api/webhooks/")
            or parsed.username
            or parsed.fragment
            or parsed.query
        ):
            raise AcceptanceConfigError("DEV_ACCEPTANCE discord needs an HTTPS Discord webhook")
    if discord and not speakers:
        raise AcceptanceConfigError("DEV_ACCEPTANCE discord requires selected speakers")
    if speakers:
        media_address(values)
    return data


def media_address(values=None):
    values = os.environ if values is None else values
    try:
        origin = urlsplit(values.get("DEV_ACCEPTANCE_MEDIA_ORIGIN") or "")
        host = ipaddress.ip_address(origin.hostname or "")
        port = origin.port
    except ValueError:
        raise AcceptanceConfigError(
            "DEV_ACCEPTANCE_MEDIA_ORIGIN needs a literal LAN IP and separate port"
        ) from None
    if (
        origin.scheme != "http"
        or not port
        or origin.username
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
        or not (host.is_private or host.is_loopback)
        or host.is_unspecified
        or host.is_multicast
        or port in {5555, 5556, int(values.get("PORT") or 5557)}
    ):
        raise AcceptanceConfigError("DEV_ACCEPTANCE_MEDIA_ORIGIN needs a separate LAN HTTP port")
    return str(host), port


def summary(values=None):
    data = selection(values)
    targets = [
        f"speaker {t['key']} ({t['host']}:{t.get('port', 8009)})" for t in data.get("speakers", [])
    ]
    if data.get("discord"):
        targets.append(f"Discord {data['discord']['label']}")
    return "Acceptance: " + (", ".join(targets) if targets else "off (safe development)")


def problems(values):
    try:
        selection(values)
    except AcceptanceConfigError as exc:
        return [str(exc)]
    return []


def initialize(app):
    """Build the actual candidate with only selected destinations, or refuse."""
    from .environment import is_development

    if not is_development():
        return None
    data = selection()
    if not data.get("speakers"):
        return None
    # Import failure is fatal: never fall back to the production builder.
    try:
        from .services.announcements import AnnouncementService
        from .services.announcements.config import (
            AnnouncementConfig,
            SpeakerTarget,
            piper_settings_from_env,
        )
        from .services.announcements.fallback import DiscordFallbackChannel
        from .services.announcements.synthesis import PiperSubprocessSynthesizer
        from .services.announcements.transport import PyChromecastTransport
    except ImportError:
        raise AcceptanceConfigError(
            "Selected worktree lacks the compatible announcement candidate"
        ) from None
    host, port = media_address()
    config = AnnouncementConfig(
        media_origin=f"http://{host}:{port}",
        public_origin="https://dev.jannenkoti.com",
        targets=tuple(SpeakerTarget(**target) for target in data["speakers"]),
        cache_dir=Path(os.environ["DEV_STATE_DIR"]) / "announcement-cache",
        media_grant_ttl_s=120,
    )
    service = AnnouncementService(
        app.ctrl,
        config=config,
        synthesizer=PiperSubprocessSynthesizer(**piper_settings_from_env()),
        transport=PyChromecastTransport(),
        fallback_channel=DiscordFallbackChannel((data.get("discord") or {}).get("webhook")),
        # Default candidate Spotify has no HTTP client/key: inherited credentials
        # cannot enable OAuth, account queries or restoration.
    )
    app.announcement_service = service
    app.development_media_server = start_media_listener(service, host, port)
    service.start()
    return service


def start_media_listener(service, host, port):
    """Share capability state in-process; expose no management routes/cookies."""
    import eventlet
    from eventlet import wsgi
    from flask import Flask, abort, send_file

    media = Flask("development-acceptance-media", static_folder=None)

    @media.get("/announcement-media/<token>.wav")
    def audio(token):
        path = service.open_media(token)
        if path is None:
            abort(404)
        response = send_file(path, mimetype="audio/wav", conditional=True)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Werkzeug's native selector loop blocks Eventlet's hub. Use the same
    # cooperative server family as the main app, without capability URL logs.
    listener = eventlet.listen((host, port))
    green = eventlet.spawn(wsgi.server, listener, media, log_output=False)

    class MediaListener:
        def shutdown(self):
            listener.close()
            green.kill()

        def server_close(self):
            listener.close()

    return MediaListener()
