#!/usr/bin/env python3
from picamera2 import Picamera2  # type: ignore[reportMissingImports]
import cv2
from time import sleep

# Initialize Picamera2 and create a preview configuration.
picam2 = Picamera2()
config = picam2.create_preview_configuration()
picam2.configure(config)
picam2.start()

# Allow the camera to settle
sleep(2)

# Infinite loop to display live feed.
while True:
    # Capture a frame from the camera.
    frame = picam2.capture_array()
    # Convert RGB to BGR for OpenCV.
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    # Display the frame.
    cv2.imshow("Live Feed", frame_bgr)
    # Exit loop if 'q' is pressed.
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

# Cleanup: release window and stop camera.
cv2.destroyAllWindows()
picam2.stop()
picam2.close()
