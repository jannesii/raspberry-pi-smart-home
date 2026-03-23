"""
ESP32 WebSocket Service

Standalone Flask service that handles WebSocket connections from ESP32 devices.
Bridges between ESP32 devices and the main app via Redis pub/sub.

Architecture:
- ESP32 connects via WebSocket to this service
- Commands from main app arrive via Redis (esp32:commands channel)
- Status from ESP32 is published to Redis (esp32:status channel)

This runs as a separate systemd service to avoid eventlet conflicts
with the main Flask-SocketIO application.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any

import flask_sock
import redis
from flask import Flask
from simple_websocket.ws import Server as SimpleWebSocketServer
from wsproto.events import (
    AcceptConnection,
    BytesMessage,
    CloseConnection,
    Ping,
    Pong,
    Request,
    TextMessage,
)
from wsproto.extensions import PerMessageDeflate
from wsproto.frame_protocol import CloseReason
from wsproto.utilities import LocalProtocolError

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
API_KEY = os.getenv("ESP32_WS_API_KEY", "")  # Required for authentication
PORT = int(os.getenv("ESP32_WS_PORT", "5556"))
HOST = os.getenv("ESP32_WS_HOST", "0.0.0.0")

# Redis channels
CHANNEL_COMMANDS = "esp32:commands"
CHANNEL_STATUS = "esp32:status"
CHANNEL_ACTION_RESULTS = "esp32:action_results"
STATUS_SUMMARY_INTERVAL_S = float(os.getenv("ESP32_WS_STATUS_LOG_INTERVAL_S", "30"))

app = Flask(__name__)


class LoggingWebSocketServer(SimpleWebSocketServer):
    """simple-websocket server variant that logs control-frame traffic."""

    def _handle_events(self):
        keep_going = True
        out_data = b""
        for event in self.ws.events():
            try:
                if isinstance(event, Request):
                    self.subprotocol = self.choose_subprotocol(event)
                    out_data += self.ws.send(
                        AcceptConnection(
                            subprotocol=self.subprotocol,
                            extensions=[PerMessageDeflate()],
                        )
                    )
                elif isinstance(event, CloseConnection):
                    if self.is_server:
                        out_data += self.ws.send(event.response())
                    self.close_reason = event.code
                    self.close_message = event.reason
                    self.connected = False
                    self.event.set()
                    keep_going = False
                elif isinstance(event, Ping):
                    manager.note_transport_event(self, "ping")
                    out_data += self.ws.send(event.response())
                elif isinstance(event, Pong):
                    self.pong_received = True
                    manager.note_transport_event(self, "pong")
                elif isinstance(event, TextMessage | BytesMessage):
                    self.incoming_message_len += len(event.data)
                    if self.max_message_size and self.incoming_message_len > self.max_message_size:
                        out_data += self.ws.send(
                            CloseConnection(
                                CloseReason.MESSAGE_TOO_BIG,
                                "Message is too big",
                            )
                        )
                        self.event.set()
                        keep_going = False
                        break
                    if self.incoming_message is None:
                        self.incoming_message = event.data
                    elif isinstance(event, TextMessage):
                        if not isinstance(self.incoming_message, bytearray):
                            self.incoming_message = bytearray(
                                (self.incoming_message + event.data).encode()
                            )
                        else:
                            self.incoming_message += event.data.encode()
                    else:
                        if not isinstance(self.incoming_message, bytearray):
                            self.incoming_message = bytearray(self.incoming_message + event.data)
                        else:
                            self.incoming_message += event.data
                    if not event.message_finished:
                        continue
                    if isinstance(self.incoming_message, str | bytes):
                        self.input_buffer.append(self.incoming_message)
                    elif isinstance(event, TextMessage):
                        self.input_buffer.append(self.incoming_message.decode())
                    else:
                        self.input_buffer.append(bytes(self.incoming_message))
                    self.incoming_message = None
                    self.incoming_message_len = 0
                    self.event.set()
                else:  # pragma: no cover
                    pass
            except LocalProtocolError:  # pragma: no cover
                out_data = b""
                self.event.set()
                keep_going = False
        if out_data:
            self.sock.send(out_data)
        return keep_going


flask_sock.Server = LoggingWebSocketServer
sock = flask_sock.Sock(app)


@dataclass
class ESP32Connection:
    """Represents a connected ESP32 device."""

    ws: Any  # WebSocket connection
    device_id: str
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_message_at: datetime | None = None
    message_count: int = 0
    last_status_at: datetime | None = None
    status_count: int = 0
    transport_ping_count: int = 0
    last_transport_ping_at: datetime | None = None
    transport_pong_count: int = 0
    last_transport_pong_at: datetime | None = None
    last_status_log_monotonic: float = 0.0


class ESP32WebSocketManager:
    """
    Manages ESP32 WebSocket connections and Redis pub/sub.

    Thread-safe management of multiple device connections.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[str, ESP32Connection] = {}  # device_id -> connection
        self._redis: redis.Redis | None = None
        self._subscriber_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Initialize Redis and start subscriber thread."""
        if self._subscriber_thread is not None and self._subscriber_thread.is_alive():
            logger.debug("ESP32 manager already running")
            return
        try:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
            self._redis.ping()
            logger.info("Redis connection established")
        except Exception as e:
            logger.error("Failed to connect to Redis: %s", e)
            raise

        self._stop_event.clear()
        self._subscriber_thread = threading.Thread(
            target=self._redis_subscriber,
            name="RedisSubscriber",
            daemon=True,
        )
        self._subscriber_thread.start()
        logger.info("Redis subscriber thread started")

    def stop(self) -> None:
        """Stop the manager and close connections."""
        logger.debug("ESP32 manager stop requested")
        self._stop_event.set()
        if self._subscriber_thread:
            self._subscriber_thread.join(timeout=5.0)

        with self._lock:
            for conn in self._connections.values():
                with contextlib.suppress(Exception):
                    conn.ws.close()
            self._connections.clear()

    def register_connection(self, device_id: str, ws: Any) -> ESP32Connection:
        """Register a new ESP32 connection."""
        logger.debug("Registering ESP32 connection device=%s", device_id)
        conn = ESP32Connection(ws=ws, device_id=device_id)
        with self._lock:
            # Close existing connection for same device
            if device_id in self._connections:
                logger.debug("Replacing existing ESP32 connection device=%s", device_id)
                with contextlib.suppress(Exception):
                    self._connections[device_id].ws.close()
            self._connections[device_id] = conn

        logger.info("ESP32 device registered: %s", device_id)
        return conn

    def unregister_connection(self, device_id: str, ws: Any | None = None) -> None:
        """Unregister an ESP32 connection if it is still the active mapping."""
        logger.debug(
            "Unregistering ESP32 connection device=%s has_ws=%s",
            device_id,
            ws is not None,
        )
        with self._lock:
            conn = self._connections.get(device_id)
            if conn is None:
                logger.debug("ESP32 device already absent during unregister: %s", device_id)
                return
            if ws is not None and conn.ws is not ws:
                logger.debug("Skipping stale ESP32 unregister for replaced device=%s", device_id)
                return
            del self._connections[device_id]
        logger.info("ESP32 device unregistered: %s", device_id)

    def get_connection(self, device_id: str) -> ESP32Connection | None:
        """Get a connection by device ID."""
        with self._lock:
            return self._connections.get(device_id)

    def get_all_connections(self) -> list[ESP32Connection]:
        """Get all active connections."""
        with self._lock:
            return list(self._connections.values())

    def publish_status(self, status: dict[str, Any]) -> None:
        """Publish ESP32 status to Redis for main app."""
        if self._redis is None:
            logger.debug(
                "Skipping ESP32 status publish device=%s because redis is unavailable",
                status.get("device_id"),
            )
            return
        try:
            self._redis.publish(CHANNEL_STATUS, json.dumps(status))
            logger.debug("Published status to Redis device=%s", status.get("device_id"))
        except Exception as e:
            logger.warning("Failed to publish status: %s", e)

    def publish_action_result(self, result: dict[str, Any]) -> None:
        """Publish action result to Redis for main app."""
        if self._redis is None:
            logger.debug(
                "Skipping action-result publish device=%s because redis is unavailable",
                result.get("device_id"),
            )
            return
        try:
            self._redis.publish(CHANNEL_ACTION_RESULTS, json.dumps(result))
            logger.debug(
                "Published action result to Redis device=%s action=%s",
                result.get("device_id"),
                result.get("action"),
            )
        except Exception as e:
            logger.warning("Failed to publish action result: %s", e)

    def note_transport_event(self, ws: Any, event_type: str) -> None:
        """Record and log low-level WebSocket control-frame traffic."""
        device_id = getattr(ws, "device_id", None) or "unauthed"
        count: int | None = None
        now = datetime.now(UTC)
        with self._lock:
            conn = self._connections.get(device_id)
            if conn is not None:
                if event_type == "ping":
                    conn.transport_ping_count += 1
                    conn.last_transport_ping_at = now
                    count = conn.transport_ping_count
                elif event_type == "pong":
                    conn.transport_pong_count += 1
                    conn.last_transport_pong_at = now
                    count = conn.transport_pong_count
        if event_type == "ping":
            logger.info("ESP32 WS ping received device=%s count=%s", device_id, count or 1)
        else:
            logger.debug("ESP32 WS pong received device=%s count=%s", device_id, count or 1)

    def note_status_update(self, device_id: str, message: dict[str, Any]) -> None:
        """Track and log status frames from an ESP32 device."""
        now = datetime.now(UTC)
        now_mono = monotonic()
        should_log = False
        status_count = 0
        message_count = 0
        with self._lock:
            conn = self._connections.get(device_id)
            if conn is None:
                logger.debug("Ignoring status metrics for unknown device=%s", device_id)
            else:
                conn.last_status_at = now
                conn.status_count += 1
                status_count = conn.status_count
                message_count = conn.message_count
                should_log = (
                    conn.status_count == 1
                    or now_mono - conn.last_status_log_monotonic >= STATUS_SUMMARY_INTERVAL_S
                )
                if should_log:
                    conn.last_status_log_monotonic = now_mono

        logger.debug(
            "ESP32 status frame device=%s keys=%s",
            device_id,
            ",".join(sorted(message.keys())),
        )
        if not should_log:
            return

        ws_stats = message.get("ws_stats")
        if not isinstance(ws_stats, dict):
            ws_stats = {}
        logger.info(
            "ESP32 status received device=%s statuses=%s messages=%s payload_ts=%s temp=%s ws_sent=%s ws_received=%s ws_commands=%s ws_uptime_ms=%s",
            device_id,
            status_count,
            message_count,
            message.get("timestamp"),
            message.get("temperature"),
            ws_stats.get("messages_sent"),
            ws_stats.get("messages_received"),
            ws_stats.get("commands_executed"),
            ws_stats.get("uptime_ms"),
        )

    def _redis_subscriber(self) -> None:
        """Subscribe to Redis commands channel and forward to ESP32."""
        if self._redis is None:
            return

        try:
            pubsub = self._redis.pubsub()
            pubsub.subscribe(CHANNEL_COMMANDS)
            logger.info("Subscribed to Redis channel: %s", CHANNEL_COMMANDS)

            while not self._stop_event.is_set():
                msg = pubsub.get_message(timeout=1.0)
                if msg is None:
                    continue

                if msg["type"] != "message":
                    continue

                try:
                    command = json.loads(msg["data"])
                    self._forward_command_to_esp32(command)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from Redis: %r", msg["data"])
                except Exception as e:
                    logger.warning("Error processing Redis message: %s", e)

        except Exception:
            logger.exception("Redis subscriber crashed")
        finally:
            with contextlib.suppress(Exception):
                pubsub.close()

    def _forward_command_to_esp32(self, command: dict[str, Any]) -> None:
        """Forward a command to all connected ESP32 devices."""
        # For now, broadcast to all devices. Could be extended to target specific devices.
        connections = self.get_all_connections()
        if not connections:
            logger.warning("No ESP32 devices connected, command dropped: %r", command)
            return

        # Wrap command in array format expected by ESP32
        message = json.dumps([command])

        for conn in connections:
            try:
                conn.ws.send(message)
                logger.info(
                    "Forwarded command to ESP32 %s: %s", conn.device_id, command.get("action")
                )
            except Exception as e:
                logger.warning("Failed to send to ESP32 %s: %s", conn.device_id, e)


# Global manager instance
manager = ESP32WebSocketManager()
manager.start()


def verify_api_key(provided_key: str) -> bool:
    """Verify the provided API key."""
    if not API_KEY:
        logger.warning("ESP32_WS_API_KEY not set, accepting all connections")
        return True
    return secrets.compare_digest(provided_key, API_KEY)


@sock.route("/ws")
def esp32_websocket(ws):
    """
    WebSocket endpoint for ESP32 devices.

    Protocol:
    1. First message must be authentication: {"auth": "<api_key>", "device_id": "<id>"}
    2. After auth, ESP32 sends status updates as JSON
    3. Server pushes commands as JSON arrays: [{"action": "turn_on"}, ...]
    """
    device_id = None
    try:
        # Wait for authentication message
        auth_msg = ws.receive(timeout=10)
        if auth_msg is None:
            logger.warning("ESP32 connection timeout waiting for auth")
            return

        try:
            auth_data = json.loads(auth_msg)
        except json.JSONDecodeError:
            logger.warning("Invalid auth JSON from ESP32")
            ws.send(json.dumps({"error": "invalid_json"}))
            return

        # Verify API key
        provided_key = auth_data.get("auth", "")
        if not verify_api_key(provided_key):
            logger.warning("ESP32 authentication failed")
            ws.send(json.dumps({"error": "unauthorized"}))
            return

        device_id = auth_data.get("device_id", f"esp32_{secrets.token_hex(4)}")

        # Register connection
        conn = manager.register_connection(device_id, ws)
        ws.device_id = device_id
        ws.send(json.dumps({"status": "authenticated", "device_id": device_id}))
        logger.info("ESP32 %s authenticated successfully", device_id)

        # Main message loop
        while True:
            data = ws.receive()
            if data is None:
                logger.info("ESP32 %s disconnected (received None)", device_id)
                break

            conn.last_message_at = datetime.now(UTC)
            conn.message_count += 1

            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from ESP32 %s: %r", device_id, data)
                continue

            # Handle different message types
            if "status" in message or "shelly" in message:
                # Status update from ESP32
                message["device_id"] = device_id
                message["received_at"] = datetime.now(UTC).isoformat()
                manager.note_status_update(device_id, message)
                manager.publish_status(message)

            if "action_results" in message:
                # Action execution results
                for result in message.get("action_results", []):
                    result["device_id"] = device_id
                    manager.publish_action_result(result)

            if "logs" in message:
                # Log data from ESP32
                logger.info("ESP32 %s logs: %s", device_id, message["logs"][:500])

    except Exception as e:
        logger.exception("ESP32 WebSocket error for %s: %s", device_id, e)
    finally:
        if device_id:
            manager.unregister_connection(device_id, ws=ws)


@app.route("/health")
def health():
    """Health check endpoint."""
    connections = manager.get_all_connections()
    logger.debug("Health endpoint called connected_devices=%s", len(connections))
    return {
        "status": "healthy",
        "connected_devices": len(connections),
        "devices": [
            {
                "device_id": c.device_id,
                "connected_at": c.connected_at.isoformat(),
                "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
                "message_count": c.message_count,
                "last_status_at": c.last_status_at.isoformat() if c.last_status_at else None,
                "status_count": c.status_count,
                "last_transport_ping_at": (
                    c.last_transport_ping_at.isoformat() if c.last_transport_ping_at else None
                ),
                "transport_ping_count": c.transport_ping_count,
                "last_transport_pong_at": (
                    c.last_transport_pong_at.isoformat() if c.last_transport_pong_at else None
                ),
                "transport_pong_count": c.transport_pong_count,
            }
            for c in connections
        ],
    }


@app.route("/")
def index():
    """Simple index page."""
    logger.debug("Index endpoint called")
    return {
        "service": "ESP32 WebSocket Service",
        "version": "1.0.0",
        "endpoints": {
            "/ws": "WebSocket endpoint for ESP32 devices",
            "/health": "Health check with connected device list",
        },
    }


def main():
    """Main entry point."""
    logger.info("Starting ESP32 WebSocket Service on %s:%d", HOST, PORT)

    if not API_KEY:
        logger.warning("⚠️  ESP32_WS_API_KEY not set - connections will not be authenticated!")

    manager.start()

    try:
        # Use Gunicorn in production, werkzeug for development
        from werkzeug.serving import run_simple

        run_simple(HOST, PORT, app, use_reloader=False, threaded=True)
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
