#include "SensorSuite.h"

#include <algorithm>
#include <initializer_list>

namespace
{
constexpr uint8_t kTmp117RegTemperature = 0x00;
constexpr uint8_t kTmp117RegDeviceId = 0x0F;
constexpr uint16_t kTmp117DeviceId = 0x0117;
constexpr uint16_t kTmp117PowerOnValue = 0x8000; // -256 C before the first conversion
constexpr float kTmp117MinC = -55.0f;
constexpr float kTmp117MaxC = 150.0f;

constexpr uint8_t kSht45CmdMeasureHighPrecision = 0xFD;
constexpr uint8_t kSht45CmdReadSerial = 0x89;
constexpr uint8_t kSht45MeasureDelayMs = 10; // datasheet max 8.3 ms
constexpr float kSht45MinC = -40.0f;
constexpr float kSht45MaxC = 125.0f;

bool readTmp117Register(I2cBus &bus, uint8_t address, uint8_t reg, uint16_t &out)
{
    uint8_t buf[2];
    if (!bus.write(address, &reg, 1) || !bus.read(address, buf, sizeof(buf)))
        return false;
    out = static_cast<uint16_t>((buf[0] << 8) | buf[1]);
    return true;
}

SensorError probeTmp117(I2cBus &bus, uint8_t address)
{
    uint16_t id = 0;
    if (!readTmp117Register(bus, address, kTmp117RegDeviceId, id))
        return SensorError::NotFound;
    return (id & 0x0FFF) == kTmp117DeviceId ? SensorError::None : SensorError::WrongIdentity;
}

uint8_t sensirionCrc8(const uint8_t *data, size_t length)
{
    uint8_t crc = 0xFF;
    for (size_t i = 0; i < length; ++i)
    {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit)
            crc = (crc & 0x80) ? static_cast<uint8_t>((crc << 1) ^ 0x31)
                               : static_cast<uint8_t>(crc << 1);
    }
    return crc;
}

// Sends one SHT45 command and reads its two CRC-protected words.
SensorError sht45Command(I2cBus &bus, uint8_t address, uint8_t cmd, uint32_t waitMs,
                         uint16_t &first, uint16_t &second)
{
    uint8_t buf[6];
    if (!bus.write(address, &cmd, 1))
        return SensorError::ReadFailed;
    bus.delayMs(waitMs);
    if (!bus.read(address, buf, sizeof(buf)))
        return SensorError::ReadFailed;
    if (sensirionCrc8(buf, 2) != buf[2] || sensirionCrc8(buf + 3, 2) != buf[5])
        return SensorError::CrcMismatch;
    first = static_cast<uint16_t>((buf[0] << 8) | buf[1]);
    second = static_cast<uint16_t>((buf[3] << 8) | buf[4]);
    return SensorError::None;
}

SensorError probeSht45(I2cBus &bus, uint8_t address)
{
    // The SHT4x family has no ID register; a serial-number read with
    // valid checksums identifies a Sensirion SHT4x at this address.
    uint16_t high = 0, low = 0;
    const SensorError error = sht45Command(bus, address, kSht45CmdReadSerial, 1, high, low);
    if (error == SensorError::ReadFailed)
        return SensorError::NotFound;
    return error == SensorError::None ? SensorError::None : SensorError::WrongIdentity;
}

SensorError readTmp117(I2cBus &bus, uint8_t address, SensorReading &out)
{
    uint16_t raw = 0;
    if (!readTmp117Register(bus, address, kTmp117RegTemperature, raw))
        return SensorError::ReadFailed;
    if (raw == kTmp117PowerOnValue)
        return SensorError::NotReady;
    const float celsius = static_cast<int16_t>(raw) * 0.0078125f;
    if (celsius < kTmp117MinC || celsius > kTmp117MaxC)
        return SensorError::OutOfRange;
    out.temperatureC = celsius;
    return SensorError::None;
}

SensorError readSht45(I2cBus &bus, uint8_t address, SensorReading &out)
{
    uint16_t tRaw = 0, rhRaw = 0;
    const SensorError error =
        sht45Command(bus, address, kSht45CmdMeasureHighPrecision, kSht45MeasureDelayMs, tRaw, rhRaw);
    if (error != SensorError::None)
        return error;
    const float celsius = -45.0f + 175.0f * tRaw / 65535.0f;
    if (celsius < kSht45MinC || celsius > kSht45MaxC)
        return SensorError::OutOfRange; // all-zero/all-one words decode here
    const float humidity = -6.0f + 125.0f * rhRaw / 65535.0f;
    out.temperatureC = celsius;
    // Datasheet: crop RH outside 0..100 % (possible near condensation).
    out.humidityPct = std::min(100.0f, std::max(0.0f, humidity));
    return SensorError::None;
}
} // namespace

SensorSuite::SensorSuite(I2cBus &bus, SensorAddresses addresses)
    : bus_(bus),
      cabin_{Kind::Sht45, addresses.cabinSht45},
      glassReference_{Kind::Tmp117, addresses.glassReferenceTmp117},
      glassComparison_{Kind::Tmp117, addresses.glassComparisonTmp117}
{
}

void SensorSuite::probe(Channel &channel, uint32_t nowMs)
{
    channel.lastError = channel.kind == Kind::Sht45 ? probeSht45(bus_, channel.address)
                                                    : probeTmp117(bus_, channel.address);
    channel.identified = channel.lastError == SensorError::None;
    channel.lastProbeMs = nowMs;
    channel.consecutiveFailures = 0;
}

void SensorSuite::begin(uint32_t nowMs)
{
    for (Channel *channel : {&cabin_, &glassReference_, &glassComparison_})
        probe(*channel, nowMs);
}

SensorReading SensorSuite::sampleChannel(Channel &channel, uint32_t nowMs)
{
    if (!channel.identified && nowMs - channel.lastProbeMs >= kReprobeIntervalMs)
        probe(channel, nowMs);

    SensorReading reading;
    SensorError error = channel.lastError;
    if (channel.identified)
    {
        error = channel.kind == Kind::Sht45 ? readSht45(bus_, channel.address, reading)
                                            : readTmp117(bus_, channel.address, reading);
        if (error == SensorError::None)
        {
            channel.consecutiveFailures = 0;
        }
        else if (++channel.consecutiveFailures >= kFailuresBeforeReprobe)
        {
            // Treat as unplugged until a later probe identifies it again.
            channel.identified = false;
            channel.lastError = SensorError::ReadFailed;
            channel.lastProbeMs = nowMs;
        }
    }

    if (error == SensorError::None)
    {
        reading.status = ReadingStatus::Ok;
        reading.error = SensorError::None;
        reading.ageMs = 0;
        channel.haveValid = true;
        channel.lastValid = reading;
        channel.lastValidMs = nowMs;
        return reading;
    }

    if (!channel.haveValid)
    {
        SensorReading missing;
        missing.error = error;
        return missing;
    }

    SensorReading cached = channel.lastValid;
    cached.status = ReadingStatus::Cached;
    cached.error = error;
    cached.ageMs = nowMs - channel.lastValidMs; // unsigned math survives millis() wrap
    return cached;
}

SensorSnapshot SensorSuite::sample(uint32_t nowMs)
{
    SensorSnapshot snap;
    snap.cabin = sampleChannel(cabin_, nowMs);
    snap.glassReference = sampleChannel(glassReference_, nowMs);
    snap.glassComparison = sampleChannel(glassComparison_, nowMs);
    return snap;
}
