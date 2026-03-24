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
from dataclasses import asdict
from datetime import UTC, datetime
from time import monotonic
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
STATUS_SUMMARY_INTERVAL_S = float(os.getenv("ESP32_REDIS_STATUS_LOG_INTERVAL_S", "30"))


class ESP32RedisBridge:
    """
    Bridge between ESP32 WebSocket service (via Redis) and main Flask app.

    Subscribes to Redis pub/sub channels and emits received messages
    to browser views via Socket.IO.
    """

    def __init__(
        self,
        app: Flask,
        socketio: SocketIO,
        sio_handler: SocketEventHandler | None = None,
    ) -> None:
        self._app = app
        self._socketio = socketio
        self._sio_handler = sio_handler
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._redis: redis.Redis | None = None
        self._status_log_state: dict[str, dict[str, float | int]] = {}

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
            logger.debug(
                "ESP32RedisBridge emitting status device=%s payload_ts=%s",
                data.get("device_id"),
                data.get("timestamp"),
            )
            from ..blueprints.api.car_heater.status import process_car_heater_status_data

            with self._app.app_context():
                normalized_status, command_status, charge_mode_state, _ = (
                    process_car_heater_status_data(
                        data,
                        commands_enabled=False,
                        is_test=False,
                        skip_db=False,
                        status_source_override="WS",
                    )
                )

                payload: dict[str, Any] = {"status": normalized_status}
                if command_status is not None:
                    payload["command_status"] = command_status
                if charge_mode_state is not None:
                    payload["charge_mode"] = charge_mode_state
                logger.debug(
                    "ESP32RedisBridge normalized status device=%s is_on=%s power=%s",
                    data.get("device_id"),
                    normalized_status.get("is_heater_on"),
                    normalized_status.get("instant_power_w"),
                )

                # If we have the socket handler, use emit_to_views for proper targeting
                if self._sio_handler is not None:
                    self._sio_handler.emit_car_heater_status_to_views(
                        normalized_status,
                        command_status,
                        charge_mode_state,
                    )
                else:
                    # Fallback to broadcast
                    self._socketio.emit("car_heater_status", payload)

            logger.debug("ESP32RedisBridge emitted car_heater_status")
            self._log_status_summary(data)

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

    def _log_status_summary(self, data: dict[str, Any]) -> None:
        """Emit a throttled status summary to prove Redis handoff is alive."""
        device_id = str(data.get("device_id") or "unknown")
        now_mono = monotonic()
        state = self._status_log_state.get(device_id, {"count": 0, "last_log": 0.0})
        count = int(state.get("count", 0)) + 1
        last_log = float(state.get("last_log", 0.0))
        should_log = count == 1 or now_mono - last_log >= STATUS_SUMMARY_INTERVAL_S
        self._status_log_state[device_id] = {
            "count": count,
            "last_log": now_mono if should_log else last_log,
        }
        if not should_log:
            return

        ws_stats = data.get("ws_stats")
        if not isinstance(ws_stats, dict):
            ws_stats = {}
        logger.info(
            "ESP32RedisBridge status device=%s redis_statuses=%s payload_ts=%s temp=%s ws_sent=%s ws_received=%s ws_commands=%s",
            device_id,
            count,
            data.get("timestamp"),
            data.get("temperature"),
            ws_stats.get("messages_sent"),
            ws_stats.get("messages_received"),
            ws_stats.get("commands_executed"),
        )

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
            logger.debug(
                "ESP32RedisBridge processing action result device=%s action=%s success=%s",
                data.get("device_id"),
                data.get("action"),
                data.get("success"),
            )
            payload = dict(data)
            payload["stage"] = "executed"
            payload["ok"] = bool(data.get("success", data.get("ok", False)))

            service = getattr(self._app, "car_heater_service", None)
            if service is not None and data.get("action"):
                service.mark_command_success([data])
                payload["command_status"] = asdict(service.get_command_status())
                logger.debug(
                    "ESP32RedisBridge updated command status action=%s status=%s",
                    data.get("action"),
                    payload["command_status"].get(data.get("action")),
                )

            if self._sio_handler is not None:
                self._sio_handler.emit_to_views("car_heater_action_result", payload)
            else:
                self._socketio.emit("car_heater_action_result", payload)

            logger.debug("ESP32RedisBridge emitted car_heater_action_result")
        except Exception as e:
            logger.warning("ESP32RedisBridge failed to emit action result: %s", e)


def init_esp32_redis_bridge(app: Flask) -> ESP32RedisBridge | None:
    """Initialize and start the ESP32 Redis bridge."""
    try:
        from ..extensions import socketio

        sio_handler = getattr(app, "sio_handler", None)
        bridge = ESP32RedisBridge(app, socketio, sio_handler)
        bridge.start()
        return bridge
    except Exception as e:
        logger.exception("Failed to initialize ESP32RedisBridge: %s", e)
        return None
