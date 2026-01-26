from gpiozero import LED, Button
import time

# Define GPIO pins
LED_PIN = 17  # GPIO pin for LED
BUTTON_PIN = 22  # GPIO pin for microswitch

# Initialize components
led = LED(LED_PIN)
button = Button(
    BUTTON_PIN, pull_up=True, bounce_time=0.05
)  # Uses internal pull-up resistor


# Function to toggle LED when button is pressed
def toggle_led():
    led.toggle()  # Toggles LED state (ON <-> OFF)
    print("Button Pressed! LED state:", "ON" if led.is_lit else "OFF")


# Attach event to button press
button.when_pressed = toggle_led

print("Press the button to toggle the LED.")
try:
    while True:
        time.sleep(0.1)  # Keep the script running
except KeyboardInterrupt:
    print("\nExiting program.")
