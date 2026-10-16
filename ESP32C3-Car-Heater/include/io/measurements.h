#ifndef MEASUREMENTS_H
#define MEASUREMENTS_H

#include <Arduino.h>

#include "SensorSuite.h"

// Starts the shared I2C bus, logs the addresses that answer, and identifies
// the cabin SHT45 and the two windshield TMP117 sensors.
void initSensors(uint8_t sda, uint8_t scl);

// Thread-safe for the HTTP and WebSocket tasks. Calls within
// kSensorSampleReuseMs reuse the previous bus sample with updated ages.
SensorSnapshot sampleSensors();

#endif
