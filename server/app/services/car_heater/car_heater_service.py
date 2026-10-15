import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import redis

from .bounded_heating import BoundedHeatingMixin

if TYPE_CHECKING:
    from ...core.controller import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Timezone for logging timestamps
_TZ = ZoneInfo("Europe/Helsinki")

# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CHANNEL_COMMANDS = "esp32:commands"
REDIS_CHANNEL_STATUS = "esp32:status"
REDIS_CHANNEL_ACTION_RESULTS = "esp32:action_results"

status_levels = [None, "queued", "sent", "success", "failed"]


@dataclass
class CommandStatus:
    turn_on: str | None = None
    turn_off: str | None = None
    get_logs: str | None = None
    esp_restart: str | None = None
    shelly_restart: str | None = None


@dataclass
class ChargeModeState:
    enabled: bool = False
    threshold_w: float = 20.0
    power_cut: bool = False
    power_cut_at: str | None = None
    last_instant_power_w: float | None = None
    seen_above_threshold: bool = False


class CarHeaterService(BoundedHeatingMixin):
    """
    In-memory command queue for the car heater ESP.

    Commands are queued by the web UI (or other callers) and consumed
    by the ESP when it POSTs status updates. The service maintains an
    internal thread for future background housekeeping but currently
    only manages the queue in a thread-safe manner.

    If a Controller is provided, ChargeModeState is persisted to the
    database on every update so it survives reboots.

    Commands are also published to Redis for the ESP32 WebSocket service
    to push to the device in real-time.
    """

    def __init__(self, controller: "Controller | None" = None, *, clock=time.time) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._commands: list[dict[str, Any]] = []
        self._command_status = CommandStatus()
        self._controller = controller
        self._departure_planner = None

        # Initialize Redis client for pub/sub
        try:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
            self._redis.ping()
            self._redis_available = True
            logger.info("Redis connection established for car heater commands")
        except Exception as e:
            logger.warning("Redis unavailable, falling back to HTTP-only mode: %s", e)
            self._redis = None
            self._redis_available = False

        # Load persisted state from DB if controller is available
        if self._controller is not None:
            self._charge_mode_state = self._controller.get_charge_mode_state()
            logger.info("Loaded ChargeModeState from DB: %r", self._charge_mode_state)
        else:
            self._charge_mode_state = ChargeModeState()

        self._init_bounded()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="CarHeaterService", daemon=True)
        self._thread.start()
        logger.info("CarHeaterService thread started")

    def turn_on(self, source: str, reason: str) -> None:
        """
        Turn the car heater ON.

        This is the single entry point for all autonomous heater-on logic.

        Args:
            source: Identifier of the calling component (e.g., 'ready_by', 'keep_at_temp', 'web_ui')
            reason: Human-readable explanation of why the heater is being turned on
        """
        with self._lock:
            self.require_mode_available()
            self._queue_heater_command("turn_on", source, reason)

    def turn_off(self, source: str, reason: str) -> None:
        """
        Turn the car heater OFF.

        This is the single entry point for all autonomous heater-off logic.

        Args:
            source: Identifier of the calling component (e.g., 'ready_by', 'charge_mode', 'web_ui')
            reason: Human-readable explanation of why the heater is being turned off
        """
        with self._lock:
            if self._bounded and self._bounded["phase"] != "stopped":
                self.stop_bounded_session(reason)
                return
            self._queue_heater_command("turn_off", source, reason)

    def _queue_heater_command(self, action: str, source: str, reason: str) -> None:
        """Internal method to queue a heater command and log it."""
        command = {
            "action": action,
            "source": source,
            "reason": reason,
            "issued_at": int(self._clock()),
            "expires_at": int(self._clock()) + 30,
        }
        if self._bounded_capability:
            command["boot_id"] = self._bounded_capability.get("boot_id")
            command["device_boot_id"] = self._bounded_capability.get("device_boot_id")
        with self._lock:
            if action == "turn_off":
                self._commands = [c for c in self._commands if c.get("action") != "turn_on"]
            self._commands.append(command)
            if isinstance(action, str) and hasattr(self._command_status, action):
                setattr(self._command_status, action, "queued")

        # Publish to Redis for real-time push via WebSocket service
        self._publish_command(command)

        # Log to persistent storage
        self._log_heater_event(action, source, reason)
        logger.info("Heater %s queued by %s: %s", action.upper(), source, reason)

    def _publish_command(self, command: dict[str, Any]) -> None:
        """Publish command to Redis for the ESP32 WebSocket service."""
        if not self._redis_available or self._redis is None:
            return
        try:
            self._redis.publish(REDIS_CHANNEL_COMMANDS, json.dumps(command))
            logger.debug("Published command to Redis: %r", command)
        except Exception as e:
            logger.warning("Failed to publish command to Redis: %s", e)

    def _log_heater_event(self, action: str, source: str, reason: str) -> None:
        """Log heater on/off event to persistent storage via Controller."""
        if self._controller is None:
            return
        try:
            message = f"Heater {action.upper()} | source={source} | {reason}"
            self._controller.log_message(message, log_type="car_heater")
        except Exception as e:
            logger.warning("Failed to log heater event to DB: %s", e)

    def queue_command(self, command: dict[str, Any]) -> None:
        """
        Queue a command to be sent to the ESP on the next status update.

        The command must be JSON-serializable (dict of simple types).
        Also publishes to Redis for real-time push via WebSocket.
        """
        if not isinstance(command, dict):
            raise TypeError("command must be a dict")
        if command.get("action") in ("turn_on", "turn_off"):
            getattr(self, command["action"])(
                command.get("source", "queue"), command.get("reason", "control")
            )
            return
        with self._lock:
            self._commands.append(command)
            action = command.get("action")
            if isinstance(action, str) and hasattr(self._command_status, action):
                setattr(self._command_status, action, "queued")

        # Publish to Redis for real-time push
        self._publish_command(command)
        logger.debug("Queued car heater command: %r", command)

    def mark_commands_sent(self, commands: list[dict[str, Any]]) -> None:
        """
        Mark the given commands as sent.

        Called from the car_heater status API after returning commands
        to the ESP.
        """
        with self._lock:
            for cmd in commands:
                action = cmd.get("action")
                if (
                    self._bounded
                    and cmd.get("session_id") == self._bounded["session_id"]
                    and not any(
                        m["kind"] == "command_sent" and m.get("action") == action
                        for m in self._bounded["milestones"]
                    )
                ):
                    self._milestone("command_sent", "http_delivery", action=action)
                if isinstance(action, str) and hasattr(self._command_status, action):
                    setattr(self._command_status, action, "sent")
        logger.debug("Marked %d car heater commands as sent", len(commands))

    def accepts_command_result(self, command: dict[str, Any]) -> bool:
        """Reject acknowledgements from superseded or already stopped sessions."""
        with self._lock:
            if not self._bounded or command.get("action") not in ("turn_on", "turn_off"):
                return True
            if self._bounded["phase"] == "stopped":
                return not command.get("session_id")
            return command.get("session_id") == self._bounded["session_id"]

    def mark_command_success(self, commands: list[dict[str, Any]]) -> None:
        """
        Mark the given commands as successfully executed.

        Called from the car_heater status API if the ESP indicates
        successful execution of commands.
        """
        with self._lock:
            for cmd in commands:
                if not self.accepts_command_result(cmd):
                    continue
                self.observe_bounded_result(cmd)
                action = cmd.get("action")
                success = bool(cmd.get("success", False))
                string = "success" if success else "failed"
                if isinstance(action, str) and hasattr(self._command_status, action):
                    setattr(self._command_status, action, string)
        logger.debug("Marked %d car heater commands as successful", len(commands))

    def get_command_status(self) -> CommandStatus:
        """Return the current status of queued commands."""
        with self._lock:
            return CommandStatus(**vars(self._command_status))

    def get_charge_mode_state(self) -> ChargeModeState:
        """Return a snapshot of the current charge mode state."""
        with self._lock:
            return ChargeModeState(**vars(self._charge_mode_state))

    def set_charge_mode_enabled(self, enabled: bool) -> ChargeModeState:
        """
        Enable or disable battery charge mode.

        When enabled, internal state related to previous runs is reset.
        """
        enabled_bool = bool(enabled)
        with self._lock:
            state = self._charge_mode_state
            state.enabled = enabled_bool
            state.last_instant_power_w = None
            state.seen_above_threshold = False
            if enabled_bool:
                state.power_cut = False
                state.power_cut_at = None
            result = ChargeModeState(**vars(state))

        # Persist to DB outside of lock
        self._persist_charge_mode_state()
        return result

    def _persist_charge_mode_state(self) -> None:
        """Persist current charge mode state to the database if controller is available."""
        if self._controller is None:
            return
        try:
            with self._lock:
                state_copy = ChargeModeState(**vars(self._charge_mode_state))
            self._controller.save_charge_mode_state(state_copy)
            logger.debug("Persisted ChargeModeState to DB")
        except Exception:
            logger.exception("Failed to persist ChargeModeState to DB")

    def handle_status_update(
        self,
        instant_power_w: float | None,
        is_heater_on: bool,
        timestamp_iso: str | None = None,
    ) -> None:
        """
        Update charge mode state based on the latest heater status.

        If charge mode is enabled and the heater has previously drawn
        at least `threshold_w` power, it will automatically queue a
        `turn_off` command once the instant power drops below the
        threshold.
        """
        should_turn_off = False
        state_changed = False
        with self._lock:
            state = self._charge_mode_state
            try:
                state.last_instant_power_w = (
                    float(instant_power_w) if instant_power_w is not None else None
                )
            except (TypeError, ValueError):
                state.last_instant_power_w = None

            if not state.enabled:
                return
            if state.last_instant_power_w is None:
                return

            # First require that we have seen power at or above the
            # threshold before considering a drop below it as "cut".
            if state.last_instant_power_w >= state.threshold_w:
                if not state.seen_above_threshold:
                    state.seen_above_threshold = True
                    state_changed = True
                return

            if not state.seen_above_threshold:
                return

            # Power has now dropped below the threshold after being
            # above it: consider this a cut and auto-queue turn_off.
            state.enabled = False
            state.power_cut = True
            state.power_cut_at = timestamp_iso
            state_changed = True
            should_turn_off = True

        # Persist state changes to DB outside of lock
        if state_changed:
            self._persist_charge_mode_state()

        if should_turn_off:
            self.turn_off(
                source="charge_mode",
                reason=f"Power dropped below {state.threshold_w}W threshold (battery charge complete)",
            )

    def get_queued_commands(self) -> list[dict[str, Any]]:
        """
        Atomically return and clear all queued commands.

        Called from the car_heater status API to respond to the ESP.
        """
        self.tick_bounded()
        self.tick_departure()
        with self._lock:
            self._commands = [
                c
                for c in self._commands
                if c.get("action") != "turn_on" or c.get("expires_at", 0) > self._clock()
            ]
            if not self._commands:
                return []
            commands = list(self._commands)
            self._commands.clear()
        logger.debug("Returning %d queued car heater commands", len(commands))
        return commands

    def peek_queued_commands(self) -> list[dict[str, Any]]:
        """
        Return a snapshot of queued commands without clearing them.

        Intended for debugging or monitoring via the HTTP API.
        """
        with self._lock:
            return list(self._commands)

    def stop(self) -> None:
        """Signal the background thread to stop (best-effort)."""
        self._stop_event.set()

    def bind_departure_planner(self, planner) -> None:
        """Use the app-owned planner for both background and transport ticks."""
        self._departure_planner = planner

    def tick_departure(self) -> None:
        if self._departure_planner is not None:
            try:
                self._departure_planner.tick()
            except Exception:
                logger.exception("Departure tick failed; retaining the original plan and finish")

    def _run(self) -> None:
        """
        Background loop.

        Currently acts as a lightweight heartbeat to allow future
        housekeeping (e.g. expiring old commands). It sleeps most of
        the time to avoid unnecessary CPU usage.
        """
        try:
            while not self._stop_event.is_set():
                # Sleep with wake-up on stop_event for low CPU usage.
                self.tick_bounded()
                self.tick_departure()
                self._stop_event.wait(timeout=1.0)
        except Exception:
            logger.exception("CarHeaterService thread crashed")
