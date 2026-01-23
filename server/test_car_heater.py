#!/usr/bin/env python3
"""
Test script for car heater status endpoint.

Simple console GUI to send test payloads to the /car_heater/status/test endpoint.
Includes a simulation mode that mimics real heater behavior.
"""
import json
import random
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List
import os

import requests

# Default endpoint URL (local test server)
DEFAULT_URL = "http://127.0.0.1:5555/api/car_heater/status/test"

# API key for authentication (set this to your actual API key)
API_KEY = os.getenv("API_KEY")


def generate_shelly_data(
    output: bool = False,
    power_w: float = 0.0,
    voltage_v: float = 230.0,
    current_a: float | None = None,
    device_temp_c: float = 25.0,
    energy_total_wh: float = 12345.0,
) -> Dict[str, Any]:
    """Generate realistic Shelly PM1 data."""
    if current_a is None:
        current_a = power_w / voltage_v if voltage_v > 0 else 0.0

    return {
        "id": 0,
        "source": "test_script",
        "output": output,
        "apower": power_w,
        "voltage": voltage_v,
        "current": round(current_a, 3),
        "aenergy": {
            "total": energy_total_wh,
            "by_minute": [round(power_w / 60, 2), 0.0, 0.0],
            "minute_ts": int(datetime.now(timezone.utc).timestamp()),
        },
        "temperature": {"tC": device_temp_c, "tF": round(device_temp_c * 9 / 5 + 32, 1)},
    }


def generate_payload(
    ambient_temp: float | None = None,
    shelly_connected: bool = True,
    heater_on: bool = False,
    power_w: float = 0.0,
) -> Dict[str, Any]:
    """Generate a complete test payload."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    payload: Dict[str, Any] = {
        "timestamp": timestamp,
        "shelly_connected": shelly_connected,
    }

    if ambient_temp is not None:
        payload["temperature"] = ambient_temp

    if shelly_connected:
        shelly = generate_shelly_data(output=heater_on, power_w=power_w)
        payload["shelly"] = json.dumps(shelly)

    return payload


def send_request(url: str, payload: Dict[str, Any], verbose: bool = True) -> List[Dict[str, Any]]:
    """Send POST request and print response. Returns list of commands from server."""
    if verbose:
        print("\n" + "=" * 60)
        print("REQUEST:")
        print("-" * 60)
        print(json.dumps(payload, indent=2))

    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    commands: List[Dict[str, Any]] = []
    try:
        response = requests.post(
            url, json=payload, headers=headers, timeout=10)
        if verbose:
            print("\n" + "-" * 60)
            print(f"RESPONSE: {response.status_code}")
            print("-" * 60)
        try:
            commands = response.json()
            if verbose:
                print(json.dumps(commands, indent=2))
        except json.JSONDecodeError:
            if verbose:
                print(response.text)
    except requests.RequestException as e:
        if verbose:
            print(f"\nERROR: {e}")

    if verbose:
        print("=" * 60 + "\n")

    return commands if isinstance(commands, list) else []


def print_menu() -> None:
    """Print the main menu."""
    print("\n" + "=" * 60)
    print("  CAR HEATER STATUS TEST TOOL")
    print("=" * 60)
    print("\nPreset payloads:")
    print("  1. Heater OFF (idle, 0W)")
    print("  2. Heater ON (charging, 1200W)")
    print("  3. Heater ON (high power, 2000W)")
    print("  4. Shelly disconnected (no data)")
    print("  5. Cold ambient temperature (-15°C)")
    print("  6. Warm ambient temperature (+10°C)")
    print("\nCustom:")
    print("  c. Custom payload builder")
    print("  r. Random realistic payload")
    print("\nSimulation:")
    print("  s. Run temperature simulation (Ctrl+C to stop)")
    print("\nOptions:")
    print("  u. Change URL (current: {url})")
    print("  q. Quit")
    print("-" * 60)


def get_float_input(prompt: str, default: float | None = None) -> float | None:
    """Get float input with optional default."""
    suffix = f" [{default}]" if default is not None else " [empty=None]"
    val = input(f"{prompt}{suffix}: ").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        print("Invalid number, using default")
        return default


def get_bool_input(prompt: str, default: bool = False) -> bool:
    """Get boolean input."""
    suffix = " [Y/n]" if default else " [y/N]"
    val = input(f"{prompt}{suffix}: ").strip().lower()
    if not val:
        return default
    return val in ("y", "yes", "1", "true")


def custom_payload_builder() -> Dict[str, Any]:
    """Interactive custom payload builder."""
    print("\n--- Custom Payload Builder ---")

    ambient_temp = get_float_input("Ambient temperature (°C)", -5.0)
    shelly_connected = get_bool_input("Shelly connected?", True)
    heater_on = False
    power_w = 0.0

    if shelly_connected:
        heater_on = get_bool_input("Heater output ON?", False)
        if heater_on:
            power_w = get_float_input("Power (W)", 1200.0) or 0.0

    return generate_payload(
        ambient_temp=ambient_temp,
        shelly_connected=shelly_connected,
        heater_on=heater_on,
        power_w=power_w,
    )


def random_payload() -> Dict[str, Any]:
    """Generate a random realistic payload."""
    ambient_temp = round(random.uniform(-20.0, 15.0), 1)
    shelly_connected = random.random() > 0.1  # 90% connected
    heater_on = random.random() > 0.5 if shelly_connected else False
    power_w = round(random.uniform(800, 2200), 1) if heater_on else 0.0

    return generate_payload(
        ambient_temp=ambient_temp,
        shelly_connected=shelly_connected,
        heater_on=heater_on,
        power_w=power_w,
    )


# Global flag for simulation loop
_simulation_running = True


def _signal_handler(signum: int, frame: Any) -> None:
    """Handle Ctrl+C during simulation."""
    global _simulation_running
    _simulation_running = False
    print("\n\n[!] Stopping simulation...")


def run_simulation(url: str) -> None:
    """
    Run a temperature simulation.

    - Heater starts ON
    - Temperature rises 1°C/sec while heater is ON
    - Temperature drops 1°C/sec while heater is OFF
    - Responds to turn_on/turn_off commands from server
    - Runs until Ctrl+C
    """
    global _simulation_running
    _simulation_running = True

    print("\n" + "=" * 60)
    print("  TEMPERATURE SIMULATION")
    print("=" * 60)
    print("\nThis simulation will:")
    print("  - Start with heater ON")
    print("  - Raise temp by 1°C/sec when heater is ON")
    print("  - Lower temp by 1°C/sec when heater is OFF")
    print("  - Handle turn_on/turn_off commands from server")
    print("  - Send 1 request per second")
    print("  - Press Ctrl+C to stop")
    print("-" * 60)

    # Get starting temperature
    start_temp = get_float_input("\nStarting ambient temperature (°C)", -10.0)
    if start_temp is None:
        start_temp = -10.0

    power_w = get_float_input("Heater power when ON (W)", 1500.0)
    if power_w is None:
        power_w = 1500.0

    print(f"\n[*] Starting simulation at {start_temp}°C with heater ON")
    print("[*] Press Ctrl+C to stop\n")

    # Set up signal handler for clean exit
    old_handler = signal.signal(signal.SIGINT, _signal_handler)

    current_temp = start_temp
    heater_on = True
    tick = 0
    energy_total = 12345.0  # Starting energy counter

    try:
        while _simulation_running:
            tick += 1

            # Update temperature based on heater state
            if heater_on:
                current_temp += 1.0
                energy_total += power_w / 3600  # Wh per second
            else:
                current_temp -= 1.0

            current_temp = round(current_temp, 1)

            # Generate and send payload
            payload = generate_payload(
                ambient_temp=current_temp,
                shelly_connected=True,
                heater_on=heater_on,
                power_w=power_w if heater_on else 0.0,
            )

            # Update energy in shelly data
            if "shelly" in payload:
                shelly_data = json.loads(payload["shelly"])
                shelly_data["aenergy"]["total"] = round(energy_total, 2)
                payload["shelly"] = json.dumps(shelly_data)

            # Print status line
            heater_status = "ON " if heater_on else "OFF"
            power_display = f"{power_w:.0f}W" if heater_on else "0W"
            print(
                f"[{tick:4d}] Temp: {current_temp:6.1f}°C | "
                f"Heater: {heater_status} ({power_display}) | ",
                end=""
            )

            # Send request (quiet mode)
            commands = send_request(url, payload, verbose=False)

            # Process commands from server
            if commands:
                for cmd in commands:
                    action = cmd.get("action", "")
                    if action == "turn_off" and heater_on:
                        heater_on = False
                        print(f"CMD: turn_off -> Heater OFF")
                    elif action == "turn_on" and not heater_on:
                        heater_on = True
                        print(f"CMD: turn_on -> Heater ON")
                    else:
                        print(f"CMD: {action}")
            else:
                print("OK")

            # Wait 1 second before next tick
            time.sleep(1.0)

    finally:
        # Restore original signal handler
        signal.signal(signal.SIGINT, old_handler)

    print(f"\n[*] Simulation ended. Final temp: {current_temp}°C")
    print(f"[*] Total ticks: {tick}")


def main() -> None:
    """Main entry point."""
    url = DEFAULT_URL

    # Check for URL argument
    if len(sys.argv) > 1:
        url = sys.argv[1]

    print(f"\nUsing endpoint: {url}")

    presets = {
        "1": lambda: generate_payload(ambient_temp=-5.0, heater_on=False, power_w=0.0),
        "2": lambda: generate_payload(ambient_temp=-5.0, heater_on=True, power_w=1200.0),
        "3": lambda: generate_payload(ambient_temp=-10.0, heater_on=True, power_w=2000.0),
        "4": lambda: generate_payload(ambient_temp=-5.0, shelly_connected=False),
        "5": lambda: generate_payload(ambient_temp=-15.0, heater_on=True, power_w=1500.0),
        "6": lambda: generate_payload(ambient_temp=10.0, heater_on=False, power_w=0.0),
    }

    while True:
        print_menu()
        print(f"\n  URL: {url}")
        choice = input("\nSelect option: ").strip().lower()

        if choice == "q":
            print("Goodbye!")
            break
        elif choice == "u":
            new_url = input(f"Enter new URL [{url}]: ").strip()
            if new_url:
                url = new_url
            print(f"URL set to: {url}")
        elif choice == "c":
            payload = custom_payload_builder()
            send_request(url, payload)
        elif choice == "r":
            payload = random_payload()
            send_request(url, payload)
        elif choice == "s":
            run_simulation(url)
        elif choice in presets:
            payload = presets[choice]()
            send_request(url, payload)
        else:
            print("Invalid option, try again.")


if __name__ == "__main__":
    main()
