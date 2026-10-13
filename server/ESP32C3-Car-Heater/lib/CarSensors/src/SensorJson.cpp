#include "SensorJson.h"

const char *readingStatusName(ReadingStatus status)
{
    switch (status)
    {
    case ReadingStatus::Ok:
        return "ok";
    case ReadingStatus::Cached:
        return "cached";
    default:
        return "missing";
    }
}

namespace
{
const char *errorName(SensorError error)
{
    switch (error)
    {
    case SensorError::NotFound:
        return "not_found";
    case SensorError::WrongIdentity:
        return "wrong_identity";
    case SensorError::ReadFailed:
        return "read_failed";
    case SensorError::CrcMismatch:
        return "crc_mismatch";
    case SensorError::OutOfRange:
        return "out_of_range";
    case SensorError::NotReady:
        return "not_ready";
    default:
        return nullptr;
    }
}

// Missing readings carry explicit nulls so the server can tell them apart
// from a sensor that this frame did not report at all.
void writeReading(JsonObject out, const SensorReading &reading, bool hasHumidity)
{
    out["status"] = readingStatusName(reading.status);
    const bool hasValue = reading.status != ReadingStatus::Missing;
    if (hasValue)
    {
        out["temperature_c"] = reading.temperatureC;
        if (hasHumidity)
            out["humidity_pct"] = reading.humidityPct;
        out["age_ms"] = reading.ageMs;
    }
    else
    {
        out["temperature_c"] = nullptr;
        if (hasHumidity)
            out["humidity_pct"] = nullptr;
        out["age_ms"] = nullptr;
    }
    if (const char *error = errorName(reading.error))
        out["error"] = error;
}
} // namespace

void writeSensorStatusJson(JsonObject status, const SensorSnapshot &snapshot)
{
    // `temperature` keeps its pre-3.0 meaning (current cabin temperature) and
    // is null instead of a retained value when the cabin read failed.
    if (snapshot.cabin.status == ReadingStatus::Ok)
        status["temperature"] = snapshot.cabin.temperatureC;
    else
        status["temperature"] = nullptr;

    JsonObject sensors = status["sensors"].to<JsonObject>();
    writeReading(sensors["cabin"].to<JsonObject>(), snapshot.cabin, true);
    writeReading(sensors["glass_reference"].to<JsonObject>(), snapshot.glassReference, false);
    writeReading(sensors["glass_comparison"].to<JsonObject>(), snapshot.glassComparison, false);
}
