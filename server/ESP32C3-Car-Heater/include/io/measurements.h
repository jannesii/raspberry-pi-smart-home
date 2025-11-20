#ifndef MEASUREMENTS_H
#define MEASUREMENTS_H

#include <Arduino.h>

struct Measurements {
    float temperature;
    float pressure;
    float altitude;
};

// Initializes BMP280 and returns true if successful
// Defaults use config-defined I2C pins/address
bool initBMP280(uint8_t address, uint8_t sda, uint8_t scl);

// Takes and returns one measurement set
Measurements takeMeasurement(bool verbose = true);

// Returns last valid measurement and its age; returns false if none yet
bool getLastMeasurement(Measurements& out, uint32_t& age_ms);

#endif
