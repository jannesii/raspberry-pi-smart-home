# Project layout

- `src/core` – system services (config, logging, time, watchdog)
- `src/heating` – heating domain (thermostat, heater task, warmup math, scheduling)
- `src/io` – edges: Wi-Fi helpers, Shelly control, sensors, LEDs, WebSocket hub
- `src/ui` – HTTP/AsyncWebServer routes and handlers
- `include/` mirrors `src/` modules with matching subfolders
- `web/src` – web UI sources; `web/dist` – gzipped assets uploaded via LittleFS (`data_dir`)
- `scripts/` – small helpers like `build_web.sh`
- `test/` – tests (unchanged)
