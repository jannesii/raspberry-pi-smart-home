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
from typing import Any

import redis
from flask import Flask
from flask_sock import Sock

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

app = Flask(__name__)
sock = Sock(app)


@dataclass
class ESP32Connection:
    """Represents a connected ESP32 device."""

    ws: Any  # WebSocket connection
    device_id: str
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_message_at: datetime | None = None
    message_count: int = 0


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
        conn = ESP32Connection(ws=ws, device_id=device_id)
        with self._lock:
            # Close existing connection for same device
            if device_id in self._connections:
                with contextlib.suppress(Exception):
                    self._connections[device_id].ws.close()
            self._connections[device_id] = conn

        logger.info("ESP32 device registered: %s", device_id)
        return conn

    def unregister_connection(self, device_id: str) -> None:
        """Unregister an ESP32 connection."""
        with self._lock:
            if device_id in self._connections:
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
            return
        try:
            self._redis.publish(CHANNEL_STATUS, json.dumps(status))
            logger.debug("Published status to Redis")
        except Exception as e:
            logger.warning("Failed to publish status: %s", e)

    def publish_action_result(self, result: dict[str, Any]) -> None:
        """Publish action result to Redis for main app."""
        if self._redis is None:
            return
        try:
            self._redis.publish(CHANNEL_ACTION_RESULTS, json.dumps(result))
            logger.debug("Published action result to Redis")
        except Exception as e:
            logger.warning("Failed to publish action result: %s", e)

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
            manager.unregister_connection(device_id)


@app.route("/health")
def health():
    """Health check endpoint."""
    connections = manager.get_all_connections()
    return {
        "status": "healthy",
        "connected_devices": len(connections),
        "devices": [
            {
                "device_id": c.device_id,
                "connected_at": c.connected_at.isoformat(),
                "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
                "message_count": c.message_count,
            }
            for c in connections
        ],
    }


@app.route("/")
def index():
    """Simple index page."""
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
