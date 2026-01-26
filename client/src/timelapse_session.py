#!/usr/bin/env python3
import os
import time
import logging
import threading
import subprocess
from datetime import datetime
from typing import Callable, Optional

import cv2
from gpiozero import LED

from .camera_manager import CameraManager
from .video_encoder import VideoEncoder
from .bambu_handler import BambuHandler

logger = logging.getLogger(__name__)


class TimelapseSession:
    """
    Manages timelapse state, photo storage, video creation,
    image callbacks, and RGB LED indicators.
    """

    def __init__(
        self,
        encoder: VideoEncoder,
        red_led: LED,
        yellow_led: LED,
        green_led: LED,
    ) -> None:
        """
        camera: CameraManager instance
        encoder: VideoEncoder instance
        red_led, yellow_led, green_led: gpiozero.LED instances
        root_dir: base directory for Photos/Timelapses
        """
        self.printer = BambuHandler()
        self.camera = None
        self.encoder = encoder
        self.red_led = red_led
        self.yellow_led = yellow_led
        self.green_led = green_led
        self.root = os.getcwd()

        self.images: list[str] = []
        self.active = False
        self.should_create = True
        self.image_callback: Optional[Callable[[bytes], None]] = None

        self.service = "live-hls.service"
        self.systemctl = ["/usr/bin/sudo", "/bin/systemctl", "--quiet"]

        # ensure directories exist
        for subdir in ("Photos", "Timelapses"):
            path = os.path.join(self.root, subdir)
            if not os.path.exists(path):
                os.makedirs(path)
                logger.info("created %s", path)

        # Purge old photos
        self.clear_photos()

        # initialize to streaming-preview LED state
        self._set_streaming_leds()

    def _set_streaming_leds(self) -> None:
        """Turn on red LED for streaming, others off."""
        self.red_led.on()
        self.yellow_led.off()
        self.green_led.off()

    def _set_active_leds(self) -> None:
        """Turn on green LED when timelapse active."""
        self.red_led.off()
        self.yellow_led.off()
        self.green_led.on()

    def _set_paused_leds(self) -> None:
        """Turn on yellow LED when timelapse paused."""
        self.red_led.off()
        self.yellow_led.on()
        self.green_led.off()

    def _set_stopped_leds(self) -> None:
        """Turn all LEDs off when timelapse stopped."""
        self.red_led.off()
        self.yellow_led.off()
        self.green_led.off()

    def _systemctl(self, *args):
        subprocess.run(self.systemctl + list(args), check=True)

    def _wait_state(self, state, timeout=5):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            res = subprocess.run(
                self.systemctl + ["is-active", self.service],
                capture_output=True,
                text=True,
            )
            logger.info("systemctl status: %s", res.stdout.strip())
            if res.stdout.strip() == state:
                return
            time.sleep(0.05)
        raise RuntimeError(f"{self.service} didn’t reach {state}")

    def start(self) -> bool:
        """
        Start the timelapse session.
        """
        try:
            self._systemctl("stop", self.service)
            time.sleep(5)
            self.camera = CameraManager()
            self.images.clear()
            logger.info("started")
            self._set_active_leds()
            self.active = True
            return True
        except Exception:
            logger.exception("error starting session")
            return False

    def stop(self, create_video: bool = True) -> bool:
        """
        Stop timelapse and optionally assemble video.
        create_video: if False, skip video creation.
        """
        try:
            self.active = False
            self.should_create = create_video
            logger.info("stopped, create_video=%s", create_video)
            self._set_stopped_leds()
            success = self.finalize()
            if self.camera:
                self.camera.shutdown()
                logger.info("camera shutdown completed")
            self._systemctl("start", self.service)
            # self._wait_state("active")
            return success
        except Exception:
            logger.exception("error stopping session")
            return False

    def capture(self) -> None:
        """
        Capture one frame, send immediately if callback set,
        and save to disk if timelapse active and not paused.
        """
        data = self.camera.capture_frame(autofocus_cycle=self.active)
        if data is None:
            return

        # send preview or timelapse frame immediately
        if self.image_callback:
            self.image_callback(data)

        # save frames for timelapse video
        if self.active and self.printer.running_and_printing:
            threading.Thread(target=self._blink_red_led, args=(False, 0.2)).start()
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = os.path.join(self.root, "Photos", f"capture_{timestamp}.jpg")
            try:
                with open(filename, "wb") as f:
                    f.write(data)
                self.images.append(filename)
                logger.info("saved %s", filename)
            except Exception:
                logger.exception("error saving image")
        else:
            logger.info(
                "Not saving image, timelapse not active or printer not printing"
            )

    def _blink_red_led(self, reverse, delay) -> None:
        """Briefly blink the red LED to indicate a capture."""
        if reverse:
            self.red_led.off()
            time.sleep(delay)
            self.red_led.on()
        else:
            self.red_led.on()
            time.sleep(delay)
            self.red_led.off()

    def clear_photos(self) -> None:
        """
        Clear all captured photos.
        """
        photos_dir = os.path.join(self.root, "Photos")
        photos = [f for f in os.listdir(photos_dir) if f.endswith(".jpg")]
        if not photos:
            logger.info("no photos to clear")
            return

        for img in photos:
            try:
                os.remove(os.path.join("Photos", img))
            except Exception:
                logger.exception("error deleting %s", img)
        logger.info("Cleared %d photos", len(photos))
        self.images.clear()

    def finalize(self) -> bool:
        """
        Assemble captured images into a video if requested,
        then clean up image files and reset LEDs.
        """
        ret = True
        if not self.images:
            logger.info("no images to assemble")
        elif self.should_create:
            fps = min(25, len(self.images)) or 1
            video_name = os.path.join(
                self.root,
                "Timelapses",
                f"{datetime.now():%Y-%m-%d_%H-%M-%S}_timelapse.mp4",
            )
            try:
                first = cv2.imread(self.images[0])
                h, w, _ = first.shape
                writer = cv2.VideoWriter(
                    video_name,
                    cv2.VideoWriter_fourcc(*"mp4v"),  # type: ignore
                    fps,
                    (w, h),
                )
                if not writer.isOpened():
                    raise RuntimeError("VideoWriter failed")
                for img in self.images:
                    frame = cv2.imread(img)
                    if frame is not None:
                        writer.write(frame)
                writer.release()
                logger.info("video created %s", video_name)
                if not self.encoder.encode(video_name):
                    logger.warning("encoder fallback, video kept as-is")
            except Exception:
                ret = False
                logger.exception("error creating video")
        else:
            logger.info("video creation skipped by user")
        times = self.camera.times if self.camera else []
        if times:
            avg_time = sum(times) / len(times)
            logger.info(
                f"autofocus cycle time: \navg: {avg_time:.2f} seconds\n max: {max(times):.2f} seconds\n min: {min(times):.2f} seconds\ncount: {len(times)}"
            )
        self.clear_photos()
        self._set_streaming_leds()

        return ret
