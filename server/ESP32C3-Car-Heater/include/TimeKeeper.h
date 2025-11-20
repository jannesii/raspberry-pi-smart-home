#pragma once

#include <Arduino.h>

namespace timekeeper {

// Initialize timekeeper and load persisted settings (e.g., timezone offset)
bool begin();

// True if device has a valid epoch base (since last boot)
bool isValid();

// Set UTC epoch seconds and mark valid. Does not change timezone offset.
void setUtc(uint64_t epochSeconds);

// Set UTC epoch and timezone offset minutes (east positive), persist offset.
void setUtcWithOffset(uint64_t epochSeconds, int16_t offsetMinutes);

// Return current UTC epoch seconds (0 if not valid)
uint64_t nowUtc();

// Timezone offset in minutes (east positive)
int16_t tzOffsetMinutes();
void setTzOffsetMinutes(int16_t minutes);

// Convenience: formatted strings (empty if time invalid)
String formatEpoch(uint64_t epoch);
String formatUtc();
String formatLocal();

// Local minutes since midnight [0..1439], or -1 if time invalid
int localMinutesOfDay();

} // namespace timekeeper
