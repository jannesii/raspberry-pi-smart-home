Project Ideas and Roadmap
=========================

This document captures potential improvements and new features for the ESP32 Temperature & Humidity Poster project. Items are grouped by theme and are intentionally incremental so you can pick and prioritize.


ID Scheme
---------

Each top-level idea has an ID in brackets to make cross-referencing easy.
Format: [GROUP-N], where GROUP is one of:
- CF (Core Features)
- API (API & Control)
- RS (Reliability & Scheduling)
- SEC (Security)
- PM (Performance & Memory)
- OBS (Observability)
- UX (Provisioning & UX)
- EXT (Extensibility)
- DEV (Developer Experience)
- NX (Suggested Next Steps)


Core Features
-------------

- [CF-1] Config persistence (NVS)
  - Persist AppConfig to NVS; load on boot; endpoint to save/discard; factory-reset endpoint/button.
- [CF-2] Offline buffering and backfill
  - Queue readings in RAM and NVS; backfill on reconnect with exponential backoff and jitter.
- [CF-3] Optional MQTT publishing
  - MQTT output alongside HTTP; Home Assistant discovery support; topic structure + retain options.
- [CF-4] Adjustable cadence
  - Make interval configurable (seconds/minutes); toggle cron-like minute alignment.


API & Control
-------------

- [API-1] HTTP API authentication
  - Bearer token for all endpoints; separate admin token for POST /config and /task; CORS disabled by default.
- [API-2] Mask secrets by default
  - GET /config masks wifi_password and api_key; add `?reveal=1` to explicitly reveal.
- [API-3] Prometheus metrics
  - GET /metrics (text/plain) with counters/gauges: posts_ok/posts_fail, last_read_time, heap, uptime, dht_fail_count, Wi‑Fi RSSI, etc.
- [API-4] Version and health endpoints
  - GET /about with build info (git SHA, build time), reset reason, firmware version; POST /reboot to trigger a safe restart.


Reliability & Scheduling
------------------------

- [RS-1] Deterministic scheduling
  - Use vTaskDelayUntil() for accurate, low-jitter minute boundaries.
- [RS-2] Watchdog and task health
  - Heartbeats per task; restart stuck tasks; log reset reason at boot.
- [RS-3] Wi‑Fi robustness
  - Multi-AP list with priorities; reconnect backoff; mDNS hostname; optional static IP configuration.


Security
--------

- [SEC-1] TLS hardening
  - Certificate pinning (fingerprint/SPKI) option; endpoint to rotate root CA; optional client certificate (mTLS).
- [SEC-2] Secure OTA updates
  - ArduinoOTA or HTTPUpdate with token protection; signed firmware; version channel and rollback.


Performance & Memory
--------------------

- [PM-1] Reduce heap fragmentation
  - Minimize dynamic String allocations; prefer fixed buffers and preallocated ArduinoJson documents.
- [PM-2] Task tuning
  - Right-size stacks; document priorities; consider CPU affinity when using dual-core boards.
- [PM-3] Async server option
  - Consider ESPAsyncWebServer to avoid blocking under concurrent requests.


Observability
-------------

- [OBS-1] Structured logging
  - Log levels (ERROR/WARN/INFO/DEBUG) with runtime toggles; ring buffer of recent logs accessible via /logs and serial.
- [OBS-2] Sensor analytics
  - Track min/max/avg over a sliding window; compute dew point/heat index; expose via /status and include in posts.


Provisioning & UX
-----------------

- [UX-1] Wi‑Fi provisioning portal
  - AP + captive portal for first boot or after long-press; store credentials in NVS; QR code for easy connection.
- [UX-2] Button/LED UX
  - Status LED patterns for Wi‑Fi/time/posting states; button: immediate read/post; long-press: reset config.


Extensibility
-------------

- [EXT-1] Multi-sensor support
  - Abstract sensor interface; support BME280/Si7021/DS18B20; per-sensor calibration; select via config.
- [EXT-2] Additional output protocols
  - InfluxDB line protocol, StatsD, Pushgateway for broader backend compatibility.
- [EXT-3] Local file logging
  - Optional CSV/JSON log to SPIFFS/SD for diagnostics and short-term buffering.


Developer Experience
--------------------

- [DEV-1] CI builds
  - GitHub Actions to build PlatformIO environments and publish firmware artifacts.
- [DEV-2] Tests
  - Unity tests for AppConfig and payload builders; integration harness for Poster with a mock server.
- [DEV-3] Documentation
  - Wiring diagram, curl examples, Postman collection, and a security hardening checklist.


Suggested Next Steps
--------------------

1) [NX-1] NVS persistence for AppConfig (save/load/factory reset) with masked secrets in GET /config.
2) [NX-2] Add bearer-token auth for HTTP API (admin + read-only tokens).
3) [NX-3] Add Prometheus-style GET /metrics for quick monitoring.
