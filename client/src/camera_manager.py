import os
import time
import logging
from typing import Optional

import cv2

# Pi‑specific libraries are imported only when we are **not** on Windows,
# so the file can be imported everywhere.
if os.name != "nt":
    from picamera2 import Picamera2  # type: ignore[reportMissingImports]
    from libcamera import controls  # type: ignore[reportMissingImports]

logger = logging.getLogger(__name__)
logger.setLevel((logging.DEBUG))


class CameraManager:
    """
    Cross‑platform camera helper.

    * On Raspberry Pi  (os.name != "nt"):  uses Picamera2.
    * On Windows        (os.name == "nt"): uses cv2.VideoCapture(0).
    """

    def __init__(
        self,
        logger: logging.Logger = logger,
        camera_index: int = 0,
        resolution: tuple[int, int] = (1920, 1080),
    ) -> None:
        self.resolution = resolution
        self._is_windows = os.name == "nt"

        if self._is_windows:
            # ----- Windows / generic webcam path -----
            self.cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not self.cap.isOpened():
                raise RuntimeError("cannot open webcam")

            # Best‑effort resolution request
            width, height = resolution
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            logger.info(
                "webcam opened (index %s) at %sx%s",
                camera_index,
                int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )

        else:
            # ----- Raspberry Pi path -----
            self.picam2 = Picamera2()  # type: ignore[reportMissingImports]
            # self.config = self.picam2.create_still_configuration()
            self.config = self.picam2.create_video_configuration(
                main={"size": (2304, 1296)}
            )
            self.picam2.configure(self.config)
            self.picam2.start_preview()
            self.picam2.start()
            time.sleep(2)  # let AGC/AWB settle
            self._enable_autofocus()
            logger.info("Picamera2 initialised")
            self.times = []

    # ------------------------------------------------------------------ #
    #  Raspberry‑Pi‑only helper
    # ------------------------------------------------------------------ #

    @staticmethod
    def af_window_frac(
        box_width_frac: float,
        box_height_frac: float,
        center_x_frac: float = 0.5,
        center_y_frac: float = 0.5,
    ) -> tuple[int, int, int, int]:
        """
        Return an AfWindows tuple (x0, y0, w, h) in Q16 sensor fractions.

        Fractions are 0.0–1.0 relative to full sensor (ScalerCropMaximum).
        Values are clamped so x0+w <= 65535, y0+h <= 65535.
        """
        Q16 = 1 << 16  # 65536
        w = int(box_width_frac * Q16)
        h = int(box_height_frac * Q16)

        # top-left from center
        x0 = int((center_x_frac - box_width_frac / 2.0) * Q16)
        y0 = int((center_y_frac - box_height_frac / 2.0) * Q16)

        # clamp into valid range
        if x0 < 0:
            x0 = 0
        if y0 < 0:
            y0 = 0
        if x0 + w > Q16 - 1:
            x0 = (Q16 - 1) - w
        if y0 + h > Q16 - 1:
            y0 = (Q16 - 1) - h

        return (x0, y0, w, h)

    def _enable_autofocus(self) -> None:
        if self._is_windows:
            return
        try:
            # center box covering ~50% of width/height
            self.af_windows = [self.af_window_frac(0.2, 0.2)]  # see helper above

            self.picam2.set_controls(
                {
                    "AfMode": controls.AfModeEnum.Continuous,
                    "AfSpeed": controls.AfSpeedEnum.Fast,
                    "AfRange": controls.AfRangeEnum.Normal,
                    "AfMetering": controls.AfMeteringEnum.Windows,  # required for AfWindows to matter
                    "AfWindows": self.af_windows,
                    "ExposureValue": -0.5,
                }
            )
            logger.info("autofocus activated (windows metering)")
        except Exception:
            logger.exception("error activating autofocus")

    def _draw_af_windows(self, frame_bgr: bytes) -> None:
        """
        Draw autofocus windows on the frame.
        This is a helper for debugging and visual confirmation.
        """
        Q16 = 1 << 16
        prev_w, prev_h = self.config["main"]["size"]

        for x0, y0, w, h in self.af_windows:  # use the value you set
            px0 = int(prev_w * (x0 / Q16))
            py0 = int(prev_h * (y0 / Q16))
            pw = int(prev_w * (w / Q16))
            ph = int(prev_h * (h / Q16))
            cv2.rectangle(frame_bgr, (px0, py0), (px0 + pw, py0 + ph), (0, 255, 0), 2)
        logger.debug("autofocus windows drawn on frame")
        cv2.imwrite("afwindow.jpg", frame_bgr)

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #
    def capture_frame(
        self, autofocus_cycle: bool = False, verbose: bool = False
    ) -> Optional[bytes]:
        """
        Grab one frame and return JPEG‑encoded bytes.
        Returns None on failure.
        """
        try:
            if self._is_windows:
                ret, frame_bgr = self.cap.read()
                if not ret:
                    raise RuntimeError("webcam read failed")
            else:
                if autofocus_cycle:
                    start = time.perf_counter()
                    success = self.picam2.autofocus_cycle()
                    elapsed = time.perf_counter() - start
                    self.times.append(elapsed)
                    if not success:
                        logger.warning(
                            f"autofocus cycle failed, using last focus, time: {elapsed:.2f} seconds"
                        )
                    else:
                        logger.debug(
                            f"autofocus cycle completed, time: {elapsed:.2f} seconds"
                        )
                # time.sleep(1.5)
                raw_rgb = self.picam2.capture_array()
                # Convert to BGR for cv2.imencode
                frame_bgr = cv2.cvtColor(raw_rgb, cv2.COLOR_RGB2BGR)

                # self._draw_af_windows(frame_bgr)
            ok, jpeg = cv2.imencode(".jpg", frame_bgr)
            if not ok:
                raise RuntimeError("JPEG encode failed")
            return jpeg.tobytes()

        except Exception:
            logger.exception("error capturing frame")
            return None

    def shutdown(self) -> None:
        """Release camera resources."""
        try:
            if self._is_windows:
                self.cap.release()
                logger.info("webcam released")
            else:
                self.picam2.stop_preview()
                self.picam2.close()
                logger.info("Picamera2 shutdown completed")
        except Exception:
            logger.exception("error during shutdown")

    # ------------------------------------------------------------------ #
    #  Context‑manager helpers (optional quality‑of‑life)
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "CameraManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    """
    example usage:
    with CameraManager() as cam:
        jpeg_bytes = cam.capture_frame()
    """
