// Simulated I2C bus with register-level TMP117 and command-level SHT45 fakes.
#pragma once

#include <cmath>
#include <cstdint>
#include <cstring>
#include <map>

#include "I2cBus.h"

inline uint8_t sensirionCrc(const uint8_t *data, size_t length)
{
    uint8_t crc = 0xFF;
    for (size_t i = 0; i < length; ++i)
    {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit)
            crc = (crc & 0x80) ? static_cast<uint8_t>((crc << 1) ^ 0x31) : static_cast<uint8_t>(crc << 1);
    }
    return crc;
}

struct FakeTmp117
{
    bool present = true;
    bool failReads = false;
    uint16_t deviceId = 0x0117;
    uint16_t temperatureRaw = 0x8000; // power-on value before the first conversion
    uint8_t pointer = 0x00;

    void setTemperature(float celsius)
    {
        temperatureRaw = static_cast<uint16_t>(static_cast<int16_t>(std::lround(celsius / 0.0078125f)));
    }
};

struct FakeSht45
{
    bool present = true;
    bool failReads = false;
    bool corruptCrc = false;
    uint16_t temperatureRaw = 0;
    uint16_t humidityRaw = 0;
    uint32_t serial = 0x1A2B3C4D;
    uint8_t lastCommand = 0;

    void set(float celsius, float humidityPct)
    {
        temperatureRaw = static_cast<uint16_t>(std::lround((celsius + 45.0f) * 65535.0f / 175.0f));
        humidityRaw = static_cast<uint16_t>(std::lround((humidityPct + 6.0f) * 65535.0f / 125.0f));
    }
};

class FakeBus : public I2cBus
{
public:
    std::map<uint8_t, FakeTmp117 *> tmp117s;
    std::map<uint8_t, FakeSht45 *> sht45s;
    uint32_t delayedMs = 0;

    bool write(uint8_t address, const uint8_t *data, size_t length) override
    {
        if (auto it = tmp117s.find(address); it != tmp117s.end() && it->second->present)
        {
            if (length >= 1)
                it->second->pointer = data[0];
            return true;
        }
        if (auto it = sht45s.find(address); it != sht45s.end() && it->second->present)
        {
            if (length >= 1)
                it->second->lastCommand = data[0];
            return true;
        }
        return false; // NACK: nothing at this address
    }

    bool read(uint8_t address, uint8_t *data, size_t length) override
    {
        if (auto it = tmp117s.find(address); it != tmp117s.end() && it->second->present)
            return readTmp117(*it->second, data, length);
        if (auto it = sht45s.find(address); it != sht45s.end() && it->second->present)
            return readSht45(*it->second, data, length);
        return false;
    }

    void delayMs(uint32_t ms) override { delayedMs += ms; }

private:
    static bool readTmp117(FakeTmp117 &dev, uint8_t *data, size_t length)
    {
        if (dev.failReads || length != 2)
            return false;
        uint16_t value = 0;
        if (dev.pointer == 0x00)
            value = dev.temperatureRaw;
        else if (dev.pointer == 0x0F)
            value = dev.deviceId;
        data[0] = static_cast<uint8_t>(value >> 8);
        data[1] = static_cast<uint8_t>(value & 0xFF);
        return true;
    }

    static void putWord(uint8_t *out, uint16_t word, bool corrupt)
    {
        out[0] = static_cast<uint8_t>(word >> 8);
        out[1] = static_cast<uint8_t>(word & 0xFF);
        out[2] = static_cast<uint8_t>(sensirionCrc(out, 2) ^ (corrupt ? 0x5A : 0x00));
    }

    static bool readSht45(FakeSht45 &dev, uint8_t *data, size_t length)
    {
        if (dev.failReads || length != 6)
            return false;
        if (dev.lastCommand == 0xFD)
        {
            putWord(data, dev.temperatureRaw, dev.corruptCrc);
            putWord(data + 3, dev.humidityRaw, dev.corruptCrc);
            return true;
        }
        if (dev.lastCommand == 0x89)
        {
            putWord(data, static_cast<uint16_t>(dev.serial >> 16), dev.corruptCrc);
            putWord(data + 3, static_cast<uint16_t>(dev.serial & 0xFFFF), dev.corruptCrc);
            return true;
        }
        return false;
    }
};
