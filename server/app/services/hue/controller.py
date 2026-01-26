#!/usr/bin/env python3
import contextlib
import logging
import os
import threading
from datetime import datetime, time as dtime

import pytz
import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class HueController:
    def __init__(self, bridge_ip, username):
        self.base_url = f"http://{bridge_ip}/api/{username}"
        self.tz = pytz.timezone("Europe/Helsinki")
        self._routine_thread = None
        self._routine_stop = None

    def get_lights(self):
        resp = requests.get(f"{self.base_url}/lights")
        resp.raise_for_status()
        return resp.json()  # { "1": {...}, "2": {...}, ... }

    def get_active_lights(self):
        lights = self.get_lights()
        return {k: v for k, v in lights.items() if v["state"]["on"]}

    def get_groups(self):
        resp = requests.get(f"{self.base_url}/groups")
        resp.raise_for_status()
        return resp.json()  # { "0": {...}, "1": {...}, ... }

    def set_light_state(self, light_id, state):
        resp = requests.put(f"{self.base_url}/lights/{light_id}/state", json=state)
        resp.raise_for_status()
        return resp.json()

    def morning_light(self, lights: dict):
        """7:00 - 10:00"""
        for light_id in lights:
            self.set_light_state(light_id, {"bri": 254, "ct": 156})

    def day_light(self, lights: dict):
        """10:00 - 17:00"""
        for light_id in lights:
            self.set_light_state(light_id, {"bri": 254, "ct": 233})

    def evening_light(self, lights: dict):
        """17:00 - 20:00"""
        for light_id in lights:
            self.set_light_state(light_id, {"bri": 254, "ct": 346})

    def late_evening_light(self, lights: dict):
        """20:00 - 23:00"""
        for light_id in lights:
            self.set_light_state(light_id, {"bri": 143, "ct": 447})

    def night_light(self, lights: dict):
        """23:00 - 7:00"""
        for light_id in lights:
            self.set_light_state(light_id, {"bri": 1, "xy": [0.561, 0.4042]})

    def start_time_based_routine(self, poll_seconds: int = 15, apply_immediately: bool = True):
        """
        Launch a background thread that monitors local time and switches *currently active* lights
        when crossing schedule boundaries:
            07:00 -> morning_light
            10:00 -> day_light
            17:00 -> evening_light
            20:00 -> late_evening_light
            23:00 -> night_light
            00:00..07:00 stays night_light
        """
        if self._routine_thread and self._routine_thread.is_alive():
            return  # already running

        self._routine_stop = threading.Event()

        def now_in_tz():
            return datetime.now(self.tz)

        def which_slot(dt: datetime):
            t = dt.time()
            slots = [
                ("morning", dtime(7, 0), dtime(10, 0), self.morning_light),
                ("day", dtime(10, 0), dtime(17, 0), self.day_light),
                ("evening", dtime(17, 0), dtime(20, 0), self.evening_light),
                ("late_evening", dtime(20, 0), dtime(23, 0), self.late_evening_light),
                ("night", dtime(23, 0), dtime(23, 59, 59, 999999), self.night_light),
                ("night", dtime(0, 0), dtime(7, 0), self.night_light),
            ]
            for name, start, end, func in slots:
                if start <= end:
                    if start <= t < end:
                        return name, func
                else:
                    # handles wrap-around segments (not used here, but kept for completeness)
                    if t >= start or t < end:
                        return name, func
            return "day", self.day_light  # safe fallback

        def apply_slot(func):
            lights = self.get_active_lights()
            if lights:
                func(lights)

        def runner():
            name, func = which_slot(now_in_tz())
            last_slot = name
            # Initial apply
            if apply_immediately:
                logger.debug("Applying initial light setting based on current time")
                with contextlib.suppress(Exception):
                    apply_slot(func)

            # Poll for boundary crossings
            while not self._routine_stop.is_set():
                name, func = which_slot(now_in_tz())
                if name != last_slot:
                    try:
                        logger.info(
                            "Time boundary crossed, applying light setting for slot '%s'", name
                        )
                        apply_slot(func)
                    except Exception:
                        pass
                    last_slot = name
                self._routine_stop.wait(poll_seconds)

        self._routine_thread = threading.Thread(target=runner, name="HueTimeRoutine", daemon=True)
        self._routine_thread.start()

    def stop_time_based_routine(self, wait: bool = False):
        """Optional helper to stop the background scheduler."""
        if self._routine_stop:
            self._routine_stop.set()
        if wait and self._routine_thread:
            self._routine_thread.join()


def main():
    # Load .env from current working directory
    load_dotenv("/etc/jannenkoti.env")

    hue_bridge_ip = os.getenv("HUE_BRIDGE_IP")
    hue_username = os.getenv("HUE_USERNAME")

    hue = HueController(hue_bridge_ip, hue_username)

    active_lights = hue.get_active_lights()
    for key, light in active_lights.items():
        print("ID", key, light)

    hue.start_time_based_routine()
    while True:
        import time

        time.sleep(1)


if __name__ == "__main__":
    main()
