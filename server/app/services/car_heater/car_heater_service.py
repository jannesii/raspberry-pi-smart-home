import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from ...core.controller import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Timezone for logging timestamps
_TZ = ZoneInfo("Europe/Helsinki")

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


class CarHeaterService:
    """
    In-memory command queue for the car heater ESP.

    Commands are queued by the web UI (or other callers) and consumed
    by the ESP when it POSTs status updates. The service maintains an
    internal thread for future background housekeeping but currently
    only manages the queue in a thread-safe manner.

    If a Controller is provided, ChargeModeState is persisted to the
    database on every update so it survives reboots.
    """

    def __init__(self, controller: "Controller | None" = None) -> None:
        self._lock = threading.Lock()
        self._commands: list[dict[str, Any]] = []
        self._command_status = CommandStatus()
        self._controller = controller

        # Load persisted state from DB if controller is available
        if self._controller is not None:
            self._charge_mode_state = self._controller.get_charge_mode_state()
            logger.info("Loaded ChargeModeState from DB: %r", self._charge_mode_state)
        else:
            self._charge_mode_state = ChargeModeState()

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
        self._queue_heater_command("turn_on", source, reason)

    def turn_off(self, source: str, reason: str) -> None:
        """
        Turn the car heater OFF.

        This is the single entry point for all autonomous heater-off logic.

        Args:
            source: Identifier of the calling component (e.g., 'ready_by', 'charge_mode', 'web_ui')
            reason: Human-readable explanation of why the heater is being turned off
        """
        self._queue_heater_command("turn_off", source, reason)

    def _queue_heater_command(self, action: str, source: str, reason: str) -> None:
        """Internal method to queue a heater command and log it."""
        command = {"action": action}
        with self._lock:
            self._commands.append(command)
            if hasattr(self._command_status, action):
                setattr(self._command_status, action, "queued")

        # Log to persistent storage
        self._log_heater_event(action, source, reason)
        logger.info("Heater %s queued by %s: %s", action.upper(), source, reason)

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
        """
        if not isinstance(command, dict):
            raise TypeError("command must be a dict")
        with self._lock:
            self._commands.append(command)
            action = command.get("action")
            setattr(self._command_status, action, "queued")
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
                if hasattr(self._command_status, action):
                    setattr(self._command_status, action, "sent")
        logger.debug("Marked %d car heater commands as sent", len(commands))

    def mark_command_success(self, commands: list[dict[str, Any]]) -> None:
        """
        Mark the given commands as successfully executed.

        Called from the car_heater status API if the ESP indicates
        successful execution of commands.
        """
        with self._lock:
            for cmd in commands:
                action = cmd.get("action")
                success = bool(cmd.get("success", False))
                string = "success" if success else "failed"
                if hasattr(self._command_status, action):
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
        with self._lock:
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
                self._stop_event.wait(timeout=60.0)
        except Exception:
            logger.exception("CarHeaterService thread crashed")
