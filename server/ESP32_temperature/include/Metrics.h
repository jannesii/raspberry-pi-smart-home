#pragma once

#include <stdint.h>

struct MetricsSnapshot
{
    uint32_t sensorReadTotal;
    uint32_t sensorReadSuccess;
    uint32_t sensorReadFailed;
    uint32_t sensorReadConsecutiveFailures;
    uint32_t lastSensorReadMillis;
    uint32_t lastSensorReadSuccessMillis;
    float lastTemperatureC;
    float lastHumidityPct;

    uint32_t postReadingTotal;
    uint32_t postReadingFailed;
    uint32_t postReadingConsecutiveFailures;
    uint32_t lastPostReadingMillis;
    uint32_t lastPostReadingSuccessMillis;

    uint32_t postErrorTotal;
    uint32_t postErrorFailed;
    uint32_t postErrorConsecutiveFailures;
    uint32_t lastPostErrorMillis;
    uint32_t lastPostErrorSuccessMillis;

    uint32_t uptimeMillis;
    uint32_t heapFreeBytes;
    uint32_t heapMinBytes;
    int32_t wifiRssiDbm;
    bool wifiConnected;
    uint32_t wifiConnectAttempts;
    uint32_t wifiReconnectEvents;
    uint32_t wifiLastAttemptMillis;
    uint32_t wifiLastConnectedMillis;
    uint32_t wifiLastDisconnectedMillis;
    uint32_t wifiCurrentBackoffMillis;
    uint32_t wifiConnectionDurationMillis;
    uint32_t wifiCurrentAttemptNumber;
};

namespace Metrics
{
    enum class PostKind : uint8_t
    {
        Reading = 0,
        Error = 1
    };

    void recordSensorRead(bool success, float temperatureC, float humidityPct);
    void recordPostResult(PostKind kind, bool success);
    void recordWifiAttempt(uint32_t attemptNumber, uint32_t backoffMs);
    void recordWifiConnected();
    void recordWifiDisconnected();
    MetricsSnapshot snapshot();
}
