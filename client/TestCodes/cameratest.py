#!/usr/bin/env python3
"""
A simple interactive script to capture images using the Raspberry Pi Camera Module 3
on a Raspberry Pi 5 with the Picamera2 library. The camera remains active and takes a picture
each time user input is given.
Before running, install Picamera2 and its dependencies.
"""

try:
    from time import sleep, time
    from picamera2 import Picamera2  # type: ignore[reportMissingImports]
    import cv2
except ImportError as e:
    missing_module = str(e).split()[-1]
    print(f"{missing_module} module not found. Please install the missing modules.")
    exit(1)


def main():
    try:
        # Initialize and configure Picamera2 for still images
        picam2 = Picamera2()
        config = picam2.create_still_configuration()
        picam2.configure(config)

        # Start the camera preview and camera stream
        picam2.start_preview()
        picam2.start()

        # Optional: Attempt to enable autofocus, if supported
        try:
            # For example, setting AfMode to 1 for continuous autofocus.
            picam2.set_controls({"AfMode": 1})
        except Exception as focus_error:
            print("Warning: Autofocus not supported or could not be set:", focus_error)

        # Allow settings (including focus adjustments) to settle
        sleep(4)

        print("Camera is ready. Press Enter to capture an image or type 'q' to quit.")
        while True:
            user_input = input(">> ")
            if user_input.lower() == "q":
                break

            # Capture the image to a NumPy array (assumed RGB format)
            image = picam2.capture_array()

            # Convert image from RGB to BGR for OpenCV
            image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

            # Create a unique filename using the current timestamp
            filename = f"capture_{int(time())}.jpg"
            cv2.imwrite(filename, image_bgr)
            print(f"Image captured and saved as {filename}")

        # Stop the preview and clean up
        picam2.stop_preview()
        picam2.close()

    except Exception as e:
        print("Error capturing image:", e)


if __name__ == "__main__":
    main()
