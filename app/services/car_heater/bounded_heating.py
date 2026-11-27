"""Durable bounded-session contract, shared by HTTP and WebSocket control."""

import copy
import math
from datetime import UTC, datetime

from .notifications import queue_problem


class BoundedHeatingMixin:
    def _init_bounded(self):
        self._next_off_retry_at = 0.0
        self._suspend_modes = None
        self._bounded = (
            self._controller.get_car_heater_execution()
            if self._controller and hasattr(self._controller, "get_car_heater_execution")
            else None
        )
        self._bounded_capability = None
        self._bounded_seen_at = 0.0
        self._capability_seen_at = 0.0
        self._no_power_since = None
        # Recovery never retransmits ON; request OFF with the original identity.
        if self._bounded:
            if (
                self._bounded["phase"] in ("starting", "heating")
                and self._clock() < self._bounded["finish_at"]
            ):
                self._notify_bounded(
                    "server_restart",
                    "Heating interrupted by a server restart. OFF is being requested; relay shutdown is not yet confirmed. Heating will not automatically resume.",
                )
            self._bounded["relay_on"] = None
            self._request_bounded_off("server_restart")

    def bind_mode_suspension(self, callback):
        self._suspend_modes = callback
        if self._bounded and self._bounded["phase"] != "stopped":
            callback()

    def get_bounded_session(self):
        with self._lock:
            run = copy.deepcopy(self._bounded)
            if not run:
                return run
            if self._clock() - self._bounded_seen_at > 30:
                run["relay_on"] = None
                run["power_w"] = None
            # Derived for the browser: the server clock judges the remaining
            # original allowance, so a skewed phone clock cannot extend it.
            start = next((m for m in run["milestones"] if m["kind"] == "start_requested"), None)
            run["mode"] = start["reason"] if start else None
            run["remaining_s"] = (
                0
                if run["phase"] == "stopped"
                else max(0, math.floor(run["finish_at"] - self._clock()))
            )
            return run

    def get_device_capability(self):
        """Latest relay-local capability report with its age, or None if none arrived."""
        with self._lock:
            if self._bounded_capability is None:
                return None
            return {
                **copy.deepcopy(self._bounded_capability),
                "age_s": max(0, math.floor(self._clock() - self._capability_seen_at)),
            }

    def _installation(self, cap):
        """The owner's saved installation record; devices do not report one."""
        return self._controller.get_car_heater_installation_snapshot()

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
        return self._start_bounded_session(duration_minutes)

    def start_departure_session(self, session_id):
        """Authorize an existing saved plan, never a new allowance on recovery."""
        plan = self._controller.get_departure_plan(session_id)
        if not plan or plan["status"] not in ("scheduled", "waiting_for_device"):
            raise ValueError("A pending saved departure plan is required")
        start = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
        finish = datetime.fromisoformat(plan["finish_at"]).timestamp()
        if not start <= self._clock() < finish or not 0 < finish - start <= 10800:
            raise ValueError("The original departure allowance is unavailable")
        if self._controller.get_car_heater_execution(session_id):
            raise ValueError("An execution attempt already exists; ON cannot resume")
        return self._start_bounded_session(plan["preheat_minutes"], plan=plan)

    def _start_bounded_session(self, duration_minutes, *, plan=None):
        with self._lock:
            self.require_mode_available()
            if plan and self._controller.get_car_heater_execution(plan["session_id"]):
                raise ValueError("An execution attempt already exists; ON cannot resume")
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
            finish = (
                datetime.fromisoformat(plan["finish_at"]).timestamp()
                if plan
                else math.floor(now + duration_minutes * 60)
            )
            if finish <= now:
                raise ValueError("Duration must allow at least one second of heating")
            attempt = (
                self._controller.get_car_heater_session(plan["session_id"])
                if plan
                else self._controller.create_car_heater_session(
                    trigger="manual",
                    observed_at=datetime.fromtimestamp(now, UTC).isoformat(),
                    requested_departure_at=None,
                    heating_duration_minutes=duration_minutes,
                    snapshot={
                        "execution_protocol": 1,
                        "firmware_version": cap.get("firmware_version"),
                        "software_version": "bounded-v1",
                        "installation": self._installation(cap),
                    },
                )
            )
            self._bounded_seen_at = 0.0
            self._no_power_since = None
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
            if plan:
                self._bounded["configuration"] = {
                    "execution_protocol": 1,
                    "firmware_version": cap.get("firmware_version"),
                    "software_version": "departure-v1",
                    "installation": self._installation(cap),
                }
            self._milestone("start_requested", "departure" if plan else "manual")
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
            command = self._bounded_command("turn_on", "departure" if plan else "manual")
            self._commands.append(command)
            self._command_status.turn_on = "queued"
            self._publish_command(command)
            return self.get_bounded_session()

    def _milestone(self, kind, reason, **evidence):
        self._bounded["milestones"].append(
            {"kind": kind, "at": self._clock(), "reason": reason, **evidence}
        )
        self._controller.save_car_heater_execution(self._bounded)

    def _notify_bounded(self, key, message):
        run = self._bounded
        return queue_problem(
            self._controller,
            run["session_id"],
            key,
            message,
            now=self._clock(),
            finish=run["finish_at"],
        )

    def _bounded_problem(self, key, active, message):
        """One warning per episode and one recovery; persist episode identity."""
        run = self._bounded
        problems = run.get("notification_problems", {})
        previous = problems.get(key, {"active": False, "episode": 0})
        if previous["active"] == active:
            return
        episode = previous["episode"] + int(active)
        if not self._notify_bounded(
            f"{key}:{episode}:{'problem' if active else 'recovery'}", message
        ):
            return
        problems[key] = {"active": active, "episode": episode}
        run["notification_problems"] = problems
        self._controller.save_car_heater_execution(run)

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
        if reason in ("execution_failed", "mode_suspension_failed"):
            self._notify_bounded(
                reason,
                "Heating could not continue because a control operation failed. OFF is being requested; relay shutdown is not yet confirmed.",
            )
        self._bounded["phase"] = "stopping"
        self._bounded["off_state"] = "pending"
        self._bounded["relay_on"] = None
        self._commands = [c for c in self._commands if c.get("action") != "turn_on"]
        self._milestone("off_requested", reason)
        command = self._bounded_command("turn_off", reason)
        self._commands = [c for c in self._commands if c.get("source") != "bounded"]
        self._commands.append(command)
        self._command_status.turn_off = "queued"
        self._next_off_retry_at = self._clock() + 5
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
            run = self._bounded
            now = self._clock()
            if run and run["phase"] in ("starting", "heating") and now < run["finish_at"]:
                seen = self._bounded_seen_at or run["issued_at"]
                stale = now - seen >= 60
                self._bounded_problem(
                    "status_lost",
                    stale,
                    "Device status has been unavailable for at least 60 seconds. Heating and relay state are unknown; the original device-local finish still applies."
                    if stale
                    else "Device status is available again. The original heating finish is unchanged.",
                )
                if not stale and run.get("relay_on") is True:
                    power = run.get("power_w")
                    if power is None or power <= 5:
                        if self._no_power_since is None:
                            self._no_power_since = now
                        if now - self._no_power_since >= 60:
                            self._bounded_problem(
                                "no_power",
                                True,
                                "The relay reports ON, but heating power above 5 W has not been confirmed for at least 60 seconds (low or missing measurements). Check the heater supply and power telemetry.",
                            )
                    elif power is not None and power > 5:
                        self._no_power_since = None
                        self._bounded_problem(
                            "no_power",
                            False,
                            "Heating power is observed again. The original heating finish is unchanged.",
                        )
                    else:
                        self._no_power_since = None
                else:
                    self._no_power_since = None
            if (
                run
                and run["phase"] == "starting"
                and now >= run["issued_at"] + 30
                and now < run["finish_at"]
            ):
                self._notify_bounded(
                    "start_unconfirmed",
                    "Heating did not confirm a start within 30 seconds. OFF is being requested; heating will not automatically resume.",
                )
            if run and run["phase"] == "stopping":
                requested = next(
                    (m["at"] for m in reversed(run["milestones"]) if m["kind"] == "off_requested"),
                    now,
                )
                if now - requested >= 30:
                    self._bounded_problem(
                        "off_unconfirmed",
                        True,
                        "OFF has not been confirmed for at least 30 seconds. OFF retries continue; the relay may still be ON. Check the device.",
                    )
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
                and self._clock() >= self._next_off_retry_at
            ):
                cmd = self._bounded_command("turn_off", "off_retry")
                self._commands = [c for c in self._commands if c.get("source") != "bounded"]
                self._commands.append(cmd)
                self._next_off_retry_at = self._clock() + 5
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
            relay = cap.get("relay_on")
            if not isinstance(relay, bool):
                run["relay_on"] = None
                run["power_w"] = None
                self._controller.save_car_heater_execution(run)
                return
            self._bounded_seen_at = self._clock()
            previous = run["relay_on"]
            if (
                relay is False
                and run["phase"] in ("starting", "heating")
                and self._clock() < run["finish_at"] - 5
                and (run["phase"] == "heating" or cap.get("phase") == "stopped")
            ):
                self._notify_bounded(
                    "early_off",
                    "Heating stopped before its original finish. The relay reports OFF. Heating will not automatically resume.",
                )
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
                self._bounded_problem(
                    "off_unconfirmed", False, "Relay OFF is now confirmed by the device."
                )
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
                if result.get("action") == "turn_off":
                    self._notify_bounded(
                        "off_failed",
                        "The device reported a failed OFF command. Relay shutdown is not confirmed; OFF retries continue.",
                    )
                if result.get("action") != "turn_off":
                    self._request_bounded_off("execution_failed")
                run["off_state"] = "failed_or_unknown"
                self._controller.save_car_heater_execution(run)
