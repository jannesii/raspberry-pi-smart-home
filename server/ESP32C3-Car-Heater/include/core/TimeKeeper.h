#pragma once

#include <Arduino.h>

namespace timekeeper {

// Initialize timekeeper and start periodic time sync (Europe/Helsinki via NTP).
// Returns true if initial time fetch succeeded.
bool begin();

// True if device has a valid time fetched from the network
bool isValid();

// Convenience: formatted strings (empty if time invalid)
// Returns local time (Europe/Helsinki) as "YYYY-MM-DD HH:MM:SS"
String formatUtc();
String formatLocal();

} // namespace timekeeper
