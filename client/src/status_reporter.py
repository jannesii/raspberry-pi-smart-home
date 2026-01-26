#!/usr/bin/env python3
import os
import sys
import json
import logging
import tempfile
import threading
import signal
from typing import Optional
from time import sleep

import requests
import re
import socketio

from .dht import DHT22Sensor
from .timelapse_session import TimelapseSession
from .bambu_handler import BambuHandler

logger = logging.getLogger(__name__)


class StatusReporter:
    """
    Handles HTTP login, Socket.IO connection, and periodic updates:
    sends status, temperature/humidity, and preview images when idle.
    """

    def __init__(
        self,
        session: TimelapseSession,
        dht: DHT22Sensor,
    ) -> None:
        """
        session: TimelapseSession instance
        dht: DHT22Sensor instance for readings
        """
        self.max_retries = 5
        self.retry_interval = 2.0

        self._is_windows = os.name == "nt"

        self.session = session
        self.dht = dht
        self.printer = BambuHandler()

        self.server = os.getenv("SERVER", "http://127.0.0.1:5555")
        self.rest = requests.Session()
        self._login()

        self.sio = socketio.Client(logger=True)
        self.sio.on("connect", self.on_connect)
        self.sio.on("disconnect", self.on_disconnect)
        self.sio.on("error", self.on_error)
        self.sio.on("timelapse_conf", self.on_config)
        self.sio.on("printerAction", self.on_printer_action)
        self.status_interval = None
        self.temphum_interval = None
        self.image_interval = None

        # events to interrupt waits
        self._status_event = threading.Event()
        self._temphum_event = threading.Event()
        self._image_event = threading.Event()

        conf = self.get_config()
        if conf:
            self.on_config(conf)

        self.tmp_dir = tempfile.gettempdir()
        self.jpg_path = os.path.join(self.tmp_dir, "preview.jpg")

        if not self._is_windows:
            self.pid = self._read_pid()

    def _read_pid(self) -> int:
        """Read the server process PID from a file."""
        pid_file = os.path.join(self.tmp_dir, "server.pid")
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())
            logger.debug(f"Read PID {pid} from {pid_file}")
            return pid
        except Exception as e:
            logger.exception(f"Could not read PID file {pid_file}: {e}")

    def _login(self) -> None:
        """Authenticate via HTTP to obtain session cookies (with CSRF)."""
        login_url = f"{self.server}/login"

        # 1) GET login page to set cookie and grab CSRF token
        resp_get = self.rest.get(login_url)
        logger.info("Fetched login page (%s) for CSRF token", login_url)
        if resp_get.status_code != 200:
            logger.error("Failed to GET login page: %s", resp_get.status_code)
            sys.exit(1)

        # 2) Extract CSRF token from hidden input
        m = re.search(r'name="csrf_token" value="([^"]+)"', resp_get.text)
        if not m:
            logger.error("CSRF token not found on login page")
            sys.exit(1)
        token = m.group(1)
        logger.debug("Using CSRF token: %s", token)

        # 3) POST credentials + CSRF
        payload = {
            "username": os.getenv("RASPI_USERNAME", "admin"),
            "password": os.getenv("RASPI_PASSWORD", "admin"),
            "csrf_token": token,
        }
        headers = {"Referer": login_url}
        logger.debug("Posting to %s with Referer header", login_url)
        resp_post = self.rest.post(login_url, data=payload, headers=headers)
        if resp_post.status_code != 200 or "Invalid credentials" in resp_post.text:
            logger.error("login failed: %s", resp_post.text)
            sys.exit(1)

        logger.info("logged in")

    def connect(self) -> None:
        """
        Establish Socket.IO connection and start background loops
        for status, temphum, and preview image streaming.
        """
        cookies = "; ".join(f"{k}={v}" for k, v in self.rest.cookies.get_dict().items())
        self.sio.connect(
            self.server,
            headers={"Cookie": cookies},
            transports=["websocket", "polling"],
            auth={"role": "client"},
        )
        threading.Thread(target=self._status_loop, daemon=True).start()
        threading.Thread(target=self._temphum_loop, daemon=True).start()
        # threading.Thread(target=self._image_loop, daemon=True).start()

        # hook timelapse capture to immediate send
        self.session.image_callback = self.save_jpg_and_signal

    def _status_loop(self) -> None:
        """Periodically emit the current timelapse status."""
        while True:
            try:
                data = self.printer.to_dict()
                data["timelapse_status"] = self.session.active
                self.sio.emit("status", data)
            except Exception:
                logger.exception("error sending status")

            woke = self._status_event.wait(timeout=self.status_interval)
            if woke:
                logger.info("status interval updated, resetting wait")
            self._status_event.clear()

    def _temphum_loop(self) -> None:
        """Periodically read DHT22 and emit temperature/humidity."""
        while True:
            try:
                data = self.dht.read()
                self.sio.emit("temphum", data)
            except Exception:
                logger.exception("error sending temphum")

            woke = self._temphum_event.wait(timeout=self.temphum_interval)
            if woke:
                logger.info("temphum interval updated, resetting wait")
            self._temphum_event.clear()

    def _image_loop(self) -> None:
        """
        Stream preview images only when timelapse is not active.
        """
        while True:
            try:
                if not self.session.active or self.printer.status != "PRINTING":
                    threading.Thread(
                        target=self.session._blink_red_led, args=(True, 0.2)
                    ).start()
                    jpeg = self.session.camera.capture_frame()
                    if jpeg:
                        with open(self.jpg_path, "wb") as f:
                            f.write(jpeg)
                        self.send_signal()
            except Exception:
                logger.exception("error in image loop")

            woke = self._image_event.wait(timeout=self.image_interval)
            if woke:
                logger.info("image interval updated, resetting wait")
            self._image_event.clear()

    def _timelapse_loop(self) -> None:
        """Continuously check for layer changes and capture images."""
        logger.info("Starting timelapse loop")
        printer_initialized = False
        self.stop_by_user = False
        while self.session.active and self.printer.gcode_status in ["RUNNING", "PAUSE"]:
            if self.printer.running_and_printing and not printer_initialized:
                logger.info("Printer initialized, starting timelapse session")
                printer_initialized = True
            status = self.printer.status
            if status != "PRINTING" and printer_initialized:
                if status not in ["CHANGING FILAMENT", "M400 PAUSE"]:
                    logger.info("Printer is not printing, stopping timelapse session")
                    break
            sleep(1)
        if printer_initialized and not self.stop_by_user:
            logger.info("Stopping timelapse session due to printer state change")
            self.session.stop(create_video=True)
        elif not printer_initialized:
            self.session.stop(create_video=False)
            logger.info("Timelapse session ended, printer not printing")

    def save_jpg_and_signal(self, data: bytes) -> None:
        """Save a preview image to disk so server can read it."""
        try:
            with open(self.jpg_path, "wb") as f:
                f.write(data)
            self.send_signal()
        except Exception:
            logger.exception("error saving image")

    def send_signal(self) -> None:
        sig_name = "SOCKETIO_IMAGE" if self._is_windows else "SIGUSR1"
        attempts = 0

        while attempts < self.max_retries:
            try:
                if True:  # self._is_windows:
                    self.sio.emit("image")
                else:
                    os.kill(self.pid, signal.SIGUSR1)
                logger.debug(f"Sent {sig_name} to server process {self.pid}")
                return  # success!

            except ProcessLookupError:
                attempts += 1
                logger.error(
                    f"[{attempts}/{self.max_retries}] "
                    f"Process {self.pid} not found, retrying..."
                )
                # re-read the PID and wait before next attempt
                self._read_pid()
                sleep(self.retry_interval)

            except Exception as e:
                logger.exception(f"Error sending {sig_name}: {e}")
                return

        # if we get here, all retries failed
        logger.error(
            f"Failed to send {sig_name} after {self.max_retries} attempts — exiting."
        )
        sys.exit(1)

    def on_connect(self) -> None:
        """Socket.IO connect handler."""
        logger.info("connected to server")

    def on_disconnect(self) -> None:
        """Socket.IO disconnect handler."""
        logger.info("disconnected from server")

    def on_error(self, data: dict) -> None:
        """Socket.IO error handler."""
        logger.error("server error: %s", data)

    def on_printer_action(self, data: dict) -> None:
        logger.info("Received printer action data: %s", data)
        action = data.get("action")
        if action not in [
            "pause",
            "resume",
            "stop",
            "home",
            "timelapse_start",
            "timelapse_stop",
            "run_gcode",
        ]:
            logger.error("invalid printer action: %s", action)
            return
        result = False
        return_data = {}
        if action == "pause":
            result = self.printer.pause_print()
        elif action == "resume":
            result = self.printer.resume_print()
        elif action == "stop":
            result = self.printer.stop_print()
        elif action == "home":
            result = self.printer.home_printer()
        elif action == "timelapse_start":
            result = False
            if not self.session.active:
                logger.info("Starting timelapse session")
                self.printer.start_timelapse()
                result = self.session.start()
            threading.Thread(target=self._timelapse_loop, daemon=True).start()
            return_data["timelapse_status"] = self.session.active
        elif action == "timelapse_stop":
            result = False
            self.stop_by_user = False
            if self.session.active:
                logger.info("Stopping timelapse session by user request")
                self.printer.stop_timelapse()
                result = self.session.stop(create_video=True)
            return_data["timelapse_status"] = self.session.active
        elif action == "run_gcode":
            self.stop_by_user = True
            gcode = data.get("gcode")
            logger.info("Running G-code: %s", gcode)
            if not gcode:
                logger.error("No G-code provided for run_gcode action")
                return
            try:
                result = self.printer.run_gcode(gcode=gcode)
            except ValueError as e:
                logger.exception("Error running G-code: %s", e)
                result = False

        if return_data:
            self.sio.emit("status", return_data)

        logger.info("Printer action %s result: %s", action, result)
        self.sio.emit("printerAction", {"action": action, "result": result})

    def on_config(self, data: dict) -> None:
        """
        Handle configuration updates.
        Updates intervals and wakes sleeping loops.
        """
        changed = []
        try:
            # collect which intervals actually changed
            if self.image_interval != data["image_delay"]:
                self.image_interval = data["image_delay"]
                changed.append("_image_event")
            if self.status_interval != data["status_delay"]:
                self.status_interval = data["status_delay"]
                changed.append("_status_event")
            if self.temphum_interval != data["temphum_delay"]:
                self.temphum_interval = data["temphum_delay"]
                changed.append("_temphum_event")

            logger.info("config updated %r, changed: %s", data, changed)
            # wake up any sleeping loops so they pick up the new value immediately
            if "_status_event" in changed:
                self._status_event.set()
            if "_temphum_event" in changed:
                self._temphum_event.set()
            if "_image_event" in changed:
                self._image_event.set()

        except KeyError as e:
            logger.warning("invalid config key %s", e)

    def get_config(self) -> Optional[dict]:
        """
        Return the current configuration settings.
        """
        url = f"{self.server}/api/timelapse_config"

        resp = self.rest.get(url)
        if resp.status_code == 200:
            try:
                data = resp.json()
                logger.info("config retrieved %r", data)
                return data
            except json.JSONDecodeError:
                logger.error("error decoding JSON response")
        else:
            logger.error("failed to retrieve config, status code: %d", resp.status_code)
            return None
        return None
