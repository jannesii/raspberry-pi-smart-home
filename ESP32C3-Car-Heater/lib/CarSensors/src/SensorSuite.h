// Cabin SHT45 and two windshield TMP117 sensors behind one I2C bus.
#pragma once

#include <cstdint>

#include "I2cBus.h"

struct SensorAddresses
{
    uint8_t cabinSht45;
    uint8_t glassReferenceTmp117;
    uint8_t glassComparisonTmp117;
};

enum class ReadingStatus
{
    Ok,     // read during this sample
    Cached, // this read failed; carries the last valid value and its age
    Missing,
};

enum class SensorError
{
    None,
    NotFound,      // nothing acknowledged at the configured address
    WrongIdentity, // a device answered, but not the expected sensor type
    ReadFailed,    // the identified sensor did not complete a measurement
    CrcMismatch,   // SHT45 frame failed its checksum
    OutOfRange,    // decoded value is outside the sensor's operating range
    NotReady,      // TMP117 has not finished its first conversion
};

struct SensorReading
{
    ReadingStatus status = ReadingStatus::Missing;
    SensorError error = SensorError::NotFound;
    float temperatureC = 0.0f;
    float humidityPct = 0.0f;
    uint32_t ageMs = 0;
};

struct SensorSnapshot
{
    SensorReading cabin;
    SensorReading glassReference;
    SensorReading glassComparison;
};

class SensorSuite
{
public:
    // An unidentified sensor is probed again at most this often.
    static constexpr uint32_t kReprobeIntervalMs = 10000;
    // Consecutive failed reads before an identified sensor is probed again.
    static constexpr uint32_t kFailuresBeforeReprobe = 3;

    SensorSuite(I2cBus &bus, SensorAddresses addresses);

    void begin(uint32_t nowMs);
    SensorSnapshot sample(uint32_t nowMs);

private:
    enum class Kind
    {
        Sht45,
        Tmp117,
    };

    struct Channel
    {
        Channel(Kind sensorKind, uint8_t sensorAddress) : kind(sensorKind), address(sensorAddress) {}

        Kind kind;
        uint8_t address;
        bool identified = false;
        SensorError lastError = SensorError::NotFound;
        uint32_t lastProbeMs = 0;
        uint32_t consecutiveFailures = 0;
        bool haveValid = false;
        SensorReading lastValid;
        uint32_t lastValidMs = 0;
    };

    void probe(Channel &channel, uint32_t nowMs);
    SensorReading sampleChannel(Channel &channel, uint32_t nowMs);

    I2cBus &bus_;
    Channel cabin_;
    Channel glassReference_;
    Channel glassComparison_;
};
