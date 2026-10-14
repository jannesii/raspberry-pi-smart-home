"""Durable bounded-session contract, shared by HTTP and WebSocket control."""

import copy
import math
from datetime import UTC, datetime


class BoundedHeatingMixin:
    def _init_bounded(self):
        self._suspend_modes = None
        self._bounded = (
            self._controller.get_car_heater_execution()
            if self._controller and hasattr(self._controller, "get_car_heater_execution")
            else None
        )
        self._bounded_capability = None
        self._bounded_seen_at = 0.0
        self._capability_seen_at = 0.0
        # Recovery never retransmits ON; request OFF with the original identity.
        if self._bounded:
            self._bounded["relay_on"] = None
            self._request_bounded_off("server_restart")

    def bind_mode_suspension(self, callback):
        self._suspend_modes = callback
        if self._bounded and self._bounded["phase"] != "stopped":
            callback()

    def get_bounded_session(self):
        with self._lock:
            run = copy.deepcopy(self._bounded)
            if run and self._clock() - self._bounded_seen_at > 30:
                run["relay_on"] = None
                run["power_w"] = None
            return run

    def require_mode_available(self):
        with self._lock:
            if self._bounded and self._bounded["phase"] != "stopped":
                raise ValueError(
                    "Bounded session must be confirmed stopped before another mode starts"
                )

    def start_bounded_session(self, duration_minutes=60):
        if (
            isinstance(duration_minutes, bool)
            or not isinstance(duration_minutes, int | float)
            or not math.isfinite(duration_minutes)
            or not 0 < duration_minutes <= 180
        ):
            raise ValueError("Manual duration must be positive and at most 180 minutes")
        with self._lock:
            self.require_mode_available()
            now = self._clock()
            cap = self._bounded_capability
            if (
                not cap
                or not cap.get("boot_id")
                or not cap.get("device_boot_id")
                or cap.get("protocol") != 1
                or cap.get("ready") is not True
                or now - self._capability_seen_at > 30
                or cap.get("relay_on") is not False
            ):
                raise ValueError(
                    "Fresh relay-local bounded capability and confirmed OFF are required"
                )
            if not self._controller:
                raise ValueError("Durable session storage is required")
            # Finish rounds down, so fractions never grant extra allowance.
            finish = math.floor(now + duration_minutes * 60)
            if finish <= now:
                raise ValueError("Duration must allow at least one second of heating")
            attempt = self._controller.create_car_heater_session(
                trigger="manual",
                observed_at=datetime.fromtimestamp(now, UTC).isoformat(),
                requested_departure_at=None,
                heating_duration_minutes=duration_minutes,
                snapshot={
                    "execution_protocol": 1,
                    "firmware_version": cap.get("firmware_version"),
                    "software_version": "bounded-v1",
                    "installation": cap.get("installation"),
                },
            )
            self._bounded = {
                "session_id": attempt.id,
                "issued_at": math.floor(now),
                "finish_at": finish,
                "boot_id": cap.get("boot_id"),
                "device_boot_id": cap.get("device_boot_id"),
                "phase": "starting",
                "relay_on": None,
                "power_w": None,
                "off_state": None,
                "milestones": [],
            }
            self._milestone("start_requested", "manual")
            self._commands = [c for c in self._commands if c.get("action") != "turn_on"]
        # Mode services may already hold their own locks while requesting relay
        # commands. Never call them under the command lock (lock inversion).
        try:
            if self._suspend_modes:
                self._suspend_modes()
        except Exception:
            self.stop_bounded_session("mode_suspension_failed")
            raise
        with self._lock:
            if self._bounded["phase"] != "starting":
                return self.get_bounded_session()
            command = self._bounded_command("turn_on", "manual")
            self._commands.append(command)
            self._command_status.turn_on = "queued"
            self._publish_command(command)
            return self.get_bounded_session()

    def _milestone(self, kind, reason, **evidence):
        self._bounded["milestones"].append(
            {"kind": kind, "at": self._clock(), "reason": reason, **evidence}
        )
        self._controller.save_car_heater_execution(self._bounded)

    def _bounded_command(self, action, reason):
        run = self._bounded
        return {
            "action": action,
            "source": "bounded",
            "reason": reason,
            "session_id": run["session_id"],
            "boot_id": run.get("boot_id"),
            "device_boot_id": run.get("device_boot_id"),
            "issued_at": run["issued_at"],
            "finish_at": run["finish_at"],
            "expires_at": min(run["issued_at"] + 30, run["finish_at"]),
        }

    def _request_bounded_off(self, reason):
        self._bounded["phase"] = "stopping"
        self._bounded["off_state"] = "pending"
        self._bounded["relay_on"] = None
        self._commands = [c for c in self._commands if c.get("action") != "turn_on"]
        self._milestone("off_requested", reason)
        command = self._bounded_command("turn_off", reason)
        self._commands = [c for c in self._commands if c.get("source") != "bounded"]
        self._commands.append(command)
        self._command_status.turn_off = "queued"
        self._publish_command(command)

    def stop_bounded_session(self, reason="cancelled"):
        with self._lock:
            if self._bounded and self._bounded["phase"] != "stopped":
                self._request_bounded_off(reason)
            else:
                self._queue_heater_command("turn_off", "manual", reason)
            return self.get_bounded_session()

    def tick_bounded(self):
        with self._lock:
            if (
                self._bounded
                and self._bounded["phase"] not in ("stopped", "stopping")
                and (
                    self._clock() >= self._bounded["finish_at"]
                    or (
                        self._bounded["phase"] == "starting"
                        and self._clock() >= self._bounded["issued_at"] + 30
                    )
                )
            ):
                self._request_bounded_off("expired")
            if (
                self._bounded
                and self._bounded["phase"] == "stopping"
                and not any(c.get("source") == "bounded" for c in self._commands)
            ):
                cmd = self._bounded_command("turn_off", "off_retry")
                self._commands.append(cmd)
                self._publish_command(cmd)

    def observe_bounded_status(self, data):
        """Only correlated relay evidence can confirm shutdown; ACKs cannot."""
        with self._lock:
            cap = data.get("bounded")
            if not isinstance(cap, dict):
                self._bounded_capability = None
                if self._bounded and self._bounded["phase"] != "stopped":
                    self._bounded["relay_on"] = None
                    self._bounded["power_w"] = None
                    self._controller.save_car_heater_execution(self._bounded)
                return
            self._bounded_capability = dict(cap)
            self._bounded_capability["firmware_version"] = data.get("firmware_version")
            self._capability_seen_at = self._clock()
            run = self._bounded
            if not run or run["phase"] == "stopped" or cap.get("session_id") != run["session_id"]:
                return
            self._bounded_seen_at = self._clock()
            relay = cap.get("relay_on")
            if not isinstance(relay, bool):
                run["relay_on"] = None
                run["power_w"] = None
                self._controller.save_car_heater_execution(run)
                return
            previous = run["relay_on"]
            run["relay_on"] = relay
            if previous != relay:
                self._milestone("relay_on" if relay else "relay_off", cap.get("reason"))
            energy = cap.get("energy_wh")
            if (
                isinstance(energy, int | float)
                and not isinstance(energy, bool)
                and math.isfinite(energy)
            ):
                if "energy_start_wh" not in run:
                    run["energy_start_wh"] = energy
                run["energy_last_wh"] = energy
            power = cap.get("power_w")
            run["power_w"] = None
            if (
                isinstance(power, int | float)
                and not isinstance(power, bool)
                and math.isfinite(power)
            ):
                run["power_w"] = power
                if power > 5 and not any(m["kind"] == "powered_on" for m in run["milestones"]):
                    self._milestone("powered_on", "observed_draw", power_w=power)
            if relay:
                if run["phase"] == "starting":
                    run["phase"] = "heating"
                    self._commands = [c for c in self._commands if c.get("action") != "turn_on"]
            elif cap.get("phase") == "stopped":
                run["phase"] = "stopped"
                run["off_state"] = "confirmed"
                self._milestone("off_confirmed", cap.get("reason"))
                self._commands = [c for c in self._commands if c.get("source") != "bounded"]
            self._controller.save_car_heater_execution(run)

    def observe_bounded_result(self, result):
        with self._lock:
            run = self._bounded
            if (
                not run
                or run["phase"] == "stopped"
                or result.get("session_id") != run["session_id"]
            ):
                return
            self._milestone(
                "command_executed",
                result.get("note"),
                action=result.get("action"),
                success=result.get("success") is True,
            )
            if result.get("success") is not True:
                self._request_bounded_off("execution_failed")
                run["off_state"] = "failed_or_unknown"
                self._controller.save_car_heater_execution(run)
