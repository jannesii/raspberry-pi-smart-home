"""
Redis bridge for ESP32 WebSocket communication.

Subscribes to Redis channels for ESP32 status updates and action results,
then emits them to browser views via Socket.IO.

This enables the separate ESP32 WebSocket service to communicate with
the main Flask app without direct coupling.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import redis

if TYPE_CHECKING:
    from flask import Flask
    from flask_socketio import SocketIO

    from ..sockets import SocketEventHandler

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Redis configuration (same as car_heater_service.py)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CHANNEL_STATUS = "esp32:status"
REDIS_CHANNEL_ACTION_RESULTS = "esp32:action_results"


class ESP32RedisBridge:
    """
    Bridge between ESP32 WebSocket service (via Redis) and main Flask app.

    Subscribes to Redis pub/sub channels and emits received messages
    to browser views via Socket.IO.
    """

    def __init__(
        self,
        socketio: SocketIO,
        sio_handler: SocketEventHandler | None = None,
    ) -> None:
        self._socketio = socketio
        self._sio_handler = sio_handler
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._redis: redis.Redis | None = None

    def start(self) -> None:
        """Start the Redis subscriber thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("ESP32RedisBridge already running")
            return

        try:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
            self._redis.ping()
            logger.info("ESP32RedisBridge: Redis connection established")
        except Exception as e:
            logger.warning("ESP32RedisBridge: Redis unavailable, bridge disabled: %s", e)
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="ESP32RedisBridge",
            daemon=True,
        )
        self._thread.start()
        logger.info("ESP32RedisBridge started")

    def stop(self) -> None:
        """Stop the Redis subscriber thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("ESP32RedisBridge stopped")

    def _run(self) -> None:
        """Main subscriber loop."""
        if self._redis is None:
            return

        try:
            pubsub = self._redis.pubsub()
            pubsub.subscribe(REDIS_CHANNEL_STATUS, REDIS_CHANNEL_ACTION_RESULTS)
            logger.info(
                "ESP32RedisBridge subscribed to channels: %s, %s",
                REDIS_CHANNEL_STATUS,
                REDIS_CHANNEL_ACTION_RESULTS,
            )

            while not self._stop_event.is_set():
                # Use get_message with timeout to allow checking stop_event
                msg = pubsub.get_message(timeout=1.0)
                if msg is None:
                    continue

                if msg["type"] != "message":
                    continue

                channel = msg["channel"]
                try:
                    data = json.loads(msg["data"])
                except json.JSONDecodeError:
                    logger.warning(
                        "ESP32RedisBridge: Invalid JSON from %s: %r", channel, msg["data"]
                    )
                    continue

                self._handle_message(channel, data)

        except Exception:
            logger.exception("ESP32RedisBridge subscriber crashed")
        finally:
            with contextlib.suppress(Exception):
                pubsub.close()

    def _handle_message(self, channel: str, data: dict[str, Any]) -> None:
        """Process a message from Redis and emit to Socket.IO."""
        logger.debug("ESP32RedisBridge received on %s: %r", channel, data)

        if channel == REDIS_CHANNEL_STATUS:
            self._emit_status(data)
        elif channel == REDIS_CHANNEL_ACTION_RESULTS:
            self._emit_action_result(data)

    def _emit_status(self, data: dict[str, Any]) -> None:
        """Emit ESP32 status update to browser views."""
        try:
            # Build payload matching existing car_heater_status format
            payload: dict[str, Any] = {"status": data}

            # If we have the socket handler, use emit_to_views for proper targeting
            if self._sio_handler is not None:
                self._sio_handler.emit_to_views("car_heater_status", payload)
            else:
                # Fallback to broadcast
                self._socketio.emit("car_heater_status", payload)

            logger.debug("ESP32RedisBridge emitted car_heater_status")

            logs_payload = self._build_logs_event_payload(
                raw_logs=data.get("logs"),
                source="ws_status",
                device_id=data.get("device_id"),
            )
            if logs_payload is None:
                logger.debug("ESP32RedisBridge status had no usable logs payload")
                return

            if self._sio_handler is not None:
                self._sio_handler.emit_to_views("car_heater_logs", logs_payload)
            else:
                self._socketio.emit("car_heater_logs", logs_payload)
            logger.debug(
                "ESP32RedisBridge emitted car_heater_logs source=%s logs_len=%s",
                logs_payload.get("source"),
                len(str(logs_payload.get("logs", ""))),
            )
        except Exception as e:
            logger.warning("ESP32RedisBridge failed to emit status: %s", e)

    def _build_logs_event_payload(
        self,
        *,
        raw_logs: Any,
        source: str,
        device_id: Any = None,
    ) -> dict[str, Any] | None:
        """Normalize logs and build the Socket.IO payload for browser views."""
        logger.debug(
            "ESP32RedisBridge._build_logs_event_payload called source=%s type=%s",
            source,
            type(raw_logs).__name__,
        )
        if raw_logs is None:
            return None
        if isinstance(raw_logs, str):
            logs_text = raw_logs.strip()
        else:
            try:
                logs_text = str(raw_logs).strip()
            except Exception:
                logger.debug(
                    "ESP32RedisBridge._build_logs_event_payload failed to coerce logs",
                    exc_info=True,
                )
                return None
        if not logs_text:
            logger.debug("ESP32RedisBridge._build_logs_event_payload skipped empty logs")
            return None

        payload: dict[str, Any] = {
            "logs": logs_text,
            "received_ts": datetime.now(UTC).isoformat(),
            "source": source,
        }
        if device_id:
            payload["device_id"] = str(device_id)
        return payload

    def _emit_action_result(self, data: dict[str, Any]) -> None:
        """Emit action result to browser views."""
        try:
            if self._sio_handler is not None:
                self._sio_handler.emit_to_views("car_heater_action_result", data)
            else:
                self._socketio.emit("car_heater_action_result", data)

            logger.debug("ESP32RedisBridge emitted car_heater_action_result")
        except Exception as e:
            logger.warning("ESP32RedisBridge failed to emit action result: %s", e)


def init_esp32_redis_bridge(app: Flask) -> ESP32RedisBridge | None:
    """Initialize and start the ESP32 Redis bridge."""
    try:
        from ..extensions import socketio

        sio_handler = getattr(app, "sio_handler", None)
        bridge = ESP32RedisBridge(socketio, sio_handler)
        bridge.start()
        return bridge
    except Exception as e:
        logger.exception("Failed to initialize ESP32RedisBridge: %s", e)
        return None
