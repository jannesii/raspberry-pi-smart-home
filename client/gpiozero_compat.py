# ---------------------------------------------------------------------------
# gpiozero‑compat shim for Windows
# ---------------------------------------------------------------------------
"""
Import this module BEFORE any `from gpiozero import LED, Button` lines.

On Raspberry Pi it just re‑exports the real gpiozero classes.
On Windows it provides no‑hardware simulation classes with the same API.
"""

import os
import sys
import threading
import time
import logging
from types import ModuleType
from typing import Callable, Optional

logger = logging.getLogger(__name__)

if os.name != "nt":
    # Pi or other Unix‑like → fall through to the real library
    import gpiozero  # type: ignore

    LED = gpiozero.LED  # noqa: N806  (exported symbols are PascalCase)
    Button = gpiozero.Button  # noqa: N806
else:
    # ----------------------------------------------------------------------
    #  Simulated classes (Windows)
    # ----------------------------------------------------------------------
    class LED:  # noqa: N801
        """
        Minimal stand‑in for gpiozero.LED.

        Methods implemented: on(), off(), blink(), close()
        """

        def __init__(self, pin: int) -> None:
            self.pin = pin
            self._state = False

        def on(self) -> None:
            self._state = True
            logger.debug("LED(%s) → ON", self.pin)

        def off(self) -> None:
            self._state = False
            logger.debug("LED(%s) → OFF", self.pin)

        def blink(
            self,
            on_time: float = 1.0,
            off_time: float = 1.0,
            n: Optional[int] = None,
            background: bool = True,
        ) -> None:
            logger.debug(
                "LED(%s) → BLINK on=%.2f off=%.2f n=%s (simulated)",
                self.pin,
                on_time,
                off_time,
                n,
            )

        def close(self) -> None:  # parity with real gpiozero objects
            logger.debug("LED(%s) closed", self.pin)

    class Button:  # noqa: N801
        """
        Simulated gpiozero.Button that fires events when the user presses
        <Enter> in the terminal window.

        Public attributes:
        ------------------
        when_pressed  : Optional[Callable[[], None]]
        when_released : Optional[Callable[[], None]]
        """

        when_pressed: Optional[Callable[[], None]] = None
        when_released: Optional[Callable[[], None]] = None

        def __init__(
            self,
            pin: int,
            pull_up: bool = True,
            bounce_time: Optional[float] = None,
        ) -> None:
            self.pin = pin
            self.pull_up = pull_up
            self.bounce_time = bounce_time
            self._pressed = False
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._key_listener, daemon=True)
            self._thread.start()
            logger.info("Button(%s) simulator ready – press <Enter> to fire", pin)

        # ------------------------------------------------------------------
        #  Basic API flags / helpers
        # ------------------------------------------------------------------
        @property
        def is_pressed(self) -> bool:  # matches gpiozero attribute
            return self._pressed

        def close(self) -> None:
            self._stop_event.set()
            self._thread.join(timeout=0.5)
            logger.debug("Button(%s) closed", self.pin)

        # ------------------------------------------------------------------
        #  Internal: keyboard‑listener loop
        # ------------------------------------------------------------------
        def _key_listener(self) -> None:
            """
            Waits for the user to hit <Enter>.  Every press triggers the
            `when_pressed` callback followed 50 ms later by `when_released`.
            """
            while not self._stop_event.is_set():
                try:
                    # Block until the user presses Enter
                    if sys.stdin.readline() == "":
                        # EOF (e.g. run from IDE) → exit thread
                        break
                except Exception:
                    break

                # Simulate press
                self._pressed = True
                if callable(self.when_pressed):
                    self.when_pressed()

                time.sleep(0.05)  # debounce / hold time

                # Simulate release
                self._pressed = False
                if callable(self.when_released):
                    self.when_released()

    # Expose a pseudo‑module so `from gpiozero import LED, Button` works
    gpiozero_stub = ModuleType("gpiozero")
    gpiozero_stub.LED = LED
    gpiozero_stub.Button = Button
    sys.modules["gpiozero"] = gpiozero_stub

# ---------------------------------------------------------------------------
# End of shim
# ---------------------------------------------------------------------------
