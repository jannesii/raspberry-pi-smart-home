// Device-frame JSON for the cabin and windshield sensors, shared by the
// HTTP fallback and WebSocket status messages.
#pragma once

#include <ArduinoJson.h>

#include "SensorSuite.h"

// "ok", "cached" or "missing", as sent in the `sensors` object.
const char *readingStatusName(ReadingStatus status);

// Writes `temperature` (legacy cabin field) and the `sensors` object.
void writeSensorStatusJson(JsonObject status, const SensorSnapshot &snapshot);
