#!/usr/bin/env python3
import time
import logging


from .dht import DHT22Sensor
from .status_reporter import StatusReporter
from .timelapse_session import TimelapseSession
from .video_encoder import VideoEncoder

from gpiozero import LED

# GPIO pins
RED_LED_PIN = 17
YELLOW_LED_PIN = 23
GREEN_LED_PIN = 27
CAPTURE_BUTTON_PIN = 22
DHT_PIN = 24

logger = logging.getLogger(__name__)


def execute_main_loop() -> None:
    """
    Application entry point: set up components and enter main loop.
    """
    red_led = LED(RED_LED_PIN)
    yellow_led = LED(YELLOW_LED_PIN)
    green_led = LED(GREEN_LED_PIN)

    # printer = BambuHandler()
    # camera = CameraManager()
    encoder = VideoEncoder()
    session = TimelapseSession(encoder, red_led, yellow_led, green_led)

    dht = DHT22Sensor(DHT_PIN)
    reporter = StatusReporter(session, dht)

    # button = Button(CAPTURE_BUTTON_PIN, pull_up=True, bounce_time=0.01)
    # handler = ButtonHandler(button, session=session)

    reporter.connect()

    logger.info("Press the button to control timelapse")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        session.finalize()
        # camera.shutdown()
