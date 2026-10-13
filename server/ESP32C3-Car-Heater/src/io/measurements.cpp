#include <Wire.h>
#include <freertos/semphr.h>

#include "io/measurements.h"
#include "SensorJson.h"
#include "io/sensorconfig.h"

namespace
{
constexpr uint32_t kSensorSampleReuseMs = 1000;
constexpr uint16_t kWireTimeoutMs = 50;

class WireI2cBus : public I2cBus
{
public:
    bool write(uint8_t address, const uint8_t *data, size_t length) override
    {
        Wire.beginTransmission(address);
        Wire.write(data, length);
        return Wire.endTransmission() == 0;
    }

    bool read(uint8_t address, uint8_t *data, size_t length) override
    {
        const size_t received = Wire.requestFrom(address, length);
        if (received != length)
        {
            while (Wire.available())
                Wire.read();
            return false;
        }
        for (size_t i = 0; i < length; ++i)
            data[i] = static_cast<uint8_t>(Wire.read());
        return true;
    }

    void delayMs(uint32_t ms) override { vTaskDelay(pdMS_TO_TICKS(ms)); }
};

WireI2cBus g_bus;
SensorSuite g_suite(g_bus, SensorAddresses{SHT45_I2C_ADDRESS,
                                           TMP117_GLASS_REFERENCE_I2C_ADDRESS,
                                           TMP117_GLASS_COMPARISON_I2C_ADDRESS});
SemaphoreHandle_t g_mutex = nullptr;
SensorSnapshot g_lastSnapshot;
uint32_t g_lastSampleMs = 0;
bool g_haveSample = false;

void logScan()
{
    Serial.print("[Sensors] I2C devices answering:");
    bool any = false;
    for (uint8_t address = 0x08; address < 0x78; ++address)
    {
        Wire.beginTransmission(address);
        if (Wire.endTransmission() == 0)
        {
            Serial.printf(" 0x%02X", address);
            any = true;
        }
    }
    Serial.println(any ? "" : " none (check VIN/GND/SDA/SCL)");
}

SensorReading aged(SensorReading reading, uint32_t extraMs)
{
    if (reading.status != ReadingStatus::Missing)
        reading.ageMs += extraMs;
    return reading;
}
} // namespace

void initSensors(uint8_t sda, uint8_t scl)
{
    g_mutex = xSemaphoreCreateMutex();
    Wire.begin(sda, scl, I2C_CLOCK_HZ);
    Wire.setTimeOut(kWireTimeoutMs);
    Serial.printf("[Sensors] I2C SDA=%u SCL=%u at %lu Hz; expecting SHT45 0x%02X, "
                  "TMP117 glass reference 0x%02X, glass comparison 0x%02X\n",
                  sda, scl, static_cast<unsigned long>(I2C_CLOCK_HZ), SHT45_I2C_ADDRESS,
                  TMP117_GLASS_REFERENCE_I2C_ADDRESS, TMP117_GLASS_COMPARISON_I2C_ADDRESS);
    logScan();
    g_suite.begin(millis());
    const SensorSnapshot snap = sampleSensors();
    Serial.printf("[Sensors] cabin=%s glass_reference=%s glass_comparison=%s\n",
                  readingStatusName(snap.cabin.status), readingStatusName(snap.glassReference.status),
                  readingStatusName(snap.glassComparison.status));
}

SensorSnapshot sampleSensors()
{
    if (g_mutex == nullptr)
        return SensorSnapshot{}; // initSensors() not called: everything missing
    xSemaphoreTake(g_mutex, portMAX_DELAY);
    const uint32_t now = millis();
    SensorSnapshot out;
    if (g_haveSample && now - g_lastSampleMs < kSensorSampleReuseMs)
    {
        const uint32_t extra = now - g_lastSampleMs;
        out.cabin = aged(g_lastSnapshot.cabin, extra);
        out.glassReference = aged(g_lastSnapshot.glassReference, extra);
        out.glassComparison = aged(g_lastSnapshot.glassComparison, extra);
    }
    else
    {
        g_lastSnapshot = g_suite.sample(now);
        g_lastSampleMs = now;
        g_haveSample = true;
        out = g_lastSnapshot;
    }
    xSemaphoreGive(g_mutex);
    return out;
}
