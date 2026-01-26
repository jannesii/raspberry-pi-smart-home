# Client – Raspberry Pi Timelapse Controller for **Bambu Lab A1**

> **Purpose**  Replace the printer‑built timelapse with a completely external, camera‑quality solution that you can tweak and extend.

The **client** directory contains everything that runs on a Raspberry Pi  (or other Linux SBC) sitting next to your Bambu Lab A1 printer.  It captures high‑resolution photos at each layer change, assembles them into an H.264 video, streams live previews & telemetry to your server, and reports printer status – all while leaving the printer’s own firmware untouched.

---

## Features

| Category | What it does |
|----------|--------------|
| **Capture** | • Shoots 1920×1080 JPEG frames with Picamera2 <br>• Automatic continuous autofocus & exposure <br>• Physical push‑button or `timelapse.gcode` trigger |
| **Timelapse session** | • Starts/pauses/stops via button multi‑click <br>• Stores raw frames in **`Photos/`** <br>• Builds a 25 fps MP4 in **`Timelapses/`** using OpenCV → re‑encodes to H.264 with `ffmpeg` |
| **Live preview** | • Streams JPEG frames over Socket.IO whenever a timelapse is not active |
| **Environment** | • Reads temperature & humidity from a DHT22 sensor and pushes them to the server |
| **UX** | • Three status LEDs (red = preview, green = recording, yellow = paused) <br>• Single GPIO push‑button with multi‑click detection |
| **Transport** | • Authenticated HTTP login + WebSocket communication <br>• Optional SFTP retrieval of finished videos with `get_timelapses.py` |

---

## Directory layout

```
client/
├── gCode/                     # Custom A1 G‑Code snippets (see below)
│   ├── start.gcode
│   ├── end.gcode
│   ├── filamentChange.gcode
│   └── timelapse.gcode
├── timelapse.py              # Main application entry‑point
├── dht.py                     # Thin CircuitPython wrapper for DHT22
├── get_timelapses.py          # Optional PC‑side script to pull videos via SFTP
├── requirements.txt           # Python dependencies (Pi‑wheels friendly)
├── setup.sh                   # One‑shot provisioning helper for Raspberry Pi
└── TestCodes/                 # ⚠ Development snippets – **not used in prod**
```

> **Note** `TestCodes/` is intentionally *not* referenced anywhere in this README, build, or runtime path.

---

## Hardware reference

| Component | BCM Pin | Notes |
|-----------|--------|-------|
| Push‑button | **22** | Short‑press patterns control the session (see below) |
| Red LED    | **17** | Steady = live preview / idle |
| Yellow LED | **23** | Steady = timelapse *paused* |
| Green LED  | **27** | Steady = timelapse *recording* |
| DHT22 data | **24** | 3 V & GND per sensor spec |

Camera: **Picamera2** (libcamera) – any HQ/IMX477 module is fine.

---

## Installation (on Raspberry Pi OS Bookworm)

```bash
# clone the repo wherever you like
git clone https://github.com/Jannnesi/Code.git
cd Code/client

# run the helper (≈10 min on Pi 4)
sudo ./setup.sh

# OR do it manually
sudo apt update && sudo apt install -y libcap-dev libatlas-base-dev ffmpeg \
      libcamera-dev libkms++-dev libfmt-dev libdrm-dev \
      python3-pip python3-dev build-essential libgpiod2
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -r requirements.txt
```

---

## Configuration

Create **`config.json`** next to `timelapse.py`:

```jsonc
{
  "server": "https://your‑server.com",     // Flask/Socket.IO backend
  "username": "pi",                        // HTTP basic login
  "password": "secret",

  "status_delay": 10,   // seconds between status emits
  "temphum_delay": 10,  // seconds between DHT22 emits
  "image_delay": 10,    // seconds between idle preview frames

  // Optional – SFTP pull helper
  "raspi_host": "raspberrypi.local",
  "raspi_port": 22,
  "remote_folders": [
    "/home/pi/Timelapses/"
  ],
  "poll_interval": 600
}
```

---

## Running

```bash
source .venv/bin/activate
python3 timelapse.py
```

### Button multi‑click map

| Clicks in < 0.3 s | Action |
|-------------------|--------|
| 1 | Capture single frame / send preview |
| 3 | **Start** timelapse, or **Stop & build video** when recording |
| 4 | Pause / resume |
| 5 | Stop **without** assembling a video |

LEDs change automatically to reflect the current state.

Photos land in **`Photos/`**; the final H.264 video is written to **`Timelapses/`**.  Both directories are created on first run.

---

## Integrating with Bambu Studio / Orca‑Slicer

1. Open *Printer Settings → Custom G‑Code*.
2. Replace the default **Start**, **End**, **Filament‑change**, and **Timelapse** snippets with the versions from `client/gCode/`.
3. The custom `timelapse.gcode` will instead poke the Raspberry Pi rig at each layer change.

These snippets park the toolhead at `X‑48 Y250 Z+0.4` for a clean, unobstructed shot, lift slightly to avoid ooze, and **comment‑out** the stock `M971/M991` timelapse hooks that would otherwise trigger the built‑in camera.

> You can tweak coordinates freely as long as they remain within the A1 print envelope.

---

## Retrieving videos automatically (optional)

Run on your workstation or NAS:

```bash
python3 get_timelapses.py
```

The script SFTP‑pulls any new MP4s from the Pi and deletes the originals once the checksum matches.

---

## Troubleshooting & tips

* **No LEDs?** Make sure you ran the script with `sudo` *or* gave the Python process GPIO capabilities (`sudo setcap cap_sys_gpio+ep $(which python3)`).
* **Video too dark?** Adjust exposure compensation inside `CameraManager.enable_autofocus()`.
* **Encoding fails?** Confirm `ffmpeg` is installed at `/usr/bin/ffmpeg` or update the path in `VideoEncoder`.
* **Socket.IO disconnects?** Double‑check that the server URL in `config.json` ends with no trailing slash and is reachable from the Pi.

---

© 2025 Janne Siirtola.  Licensed under the MIT License.
