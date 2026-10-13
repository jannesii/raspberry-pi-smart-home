// Minimal I2C transport used by the sensor drivers, so the measurement logic
// can run against a simulated bus in host tests.
#pragma once

#include <cstddef>
#include <cstdint>

class I2cBus
{
public:
    virtual ~I2cBus() = default;
    // Each call is one complete transaction; false means NACK or bus error.
    virtual bool write(uint8_t address, const uint8_t *data, size_t length) = 0;
    virtual bool read(uint8_t address, uint8_t *data, size_t length) = 0;
    virtual void delayMs(uint32_t ms) = 0;
};
