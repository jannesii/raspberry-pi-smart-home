# Maintain the ESP32 firmware in this repository

Both device firmware projects, [`ESP32_temperature/`](../../ESP32_temperature/README.md)
and [`ESP32C3-Car-Heater/`](../../ESP32C3-Car-Heater/README.md), are ordinary
tracked directories of this repository, not a submodule or an ignored nested
checkout. The server and firmware share device protocols, so changes to them are
reviewed, built and tested together; the ignored car-heater checkout was
invisible to earlier server audits. Each project keeps its own PlatformIO target,
dependencies and build instructions; this is not a shared firmware library.

The owner approved this in issue #3. It supersedes the separate-checkout note in
the defrost preparation spec (#2).

## Consequences

- The firmware histories were imported with their paths rewritten, so commit
  IDs differ from the old GitHub repositories, which are no longer maintained.
  The original tips were temperature `09d4256` (imported as `12f0df5`) and
  car heater `e995b65` (imported as `ec146ef`). A Wi-Fi password hardcoded in
  two early temperature commits was replaced during import. An unpublished
  local temperature branch, `Wifi-test`, was not imported.
- A server change that needs new firmware behavior is complete only when the
  firmware builds, the compatible deployed firmware version is recorded with the
  change, and the device has been flashed. Merging firmware source does not
  deploy it.
