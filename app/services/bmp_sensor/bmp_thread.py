import logging
import threading
import time

import requests

from ...core import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _read_bmp_sensor(ctrl: Controller):
    url = "http://192.168.10.123/bmp"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        ctrl.record_bmp_sensor_data(
            temperature=data.get("temp"),
            pressure=data.get("pressure"),
            altitude=data.get("altitude"),
        )
        logger.debug(f"BMP Sensor data: {data}")
        return data
    except requests.RequestException as e:
        logger.error(f"Error reading BMP sensor: {e}")
        return None


def _bmp_sensor_thread(ctrl: Controller, interval: int):
    logger.debug(f"BMP Sensor thread started with interval {interval}s")
    while True:
        _read_bmp_sensor(ctrl)
        time.sleep(interval)


def start_bmp_sensor_service(ctrl: Controller, interval: int):
    thread = threading.Thread(target=_bmp_sensor_thread, args=(ctrl, interval), daemon=True)
    thread.start()
