#include "Metrics.h"

#include <Arduino.h>
#include <WiFi.h>
#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

namespace
{
    struct MetricsData
    {
        uint32_t sensorReadTotal = 0;
        uint32_t sensorReadSuccess = 0;
        uint32_t sensorReadFailed = 0;
        uint32_t sensorReadConsecutiveFailures = 0;
        uint32_t lastSensorReadMillis = 0;
        uint32_t lastSensorReadSuccessMillis = 0;
        float lastTemperatureC = NAN;
        float lastHumidityPct = NAN;

        uint32_t postReadingTotal = 0;
        uint32_t postReadingFailed = 0;
        uint32_t postReadingConsecutiveFailures = 0;
        uint32_t lastPostReadingMillis = 0;
        uint32_t lastPostReadingSuccessMillis = 0;

        uint32_t postErrorTotal = 0;
        uint32_t postErrorFailed = 0;
        uint32_t postErrorConsecutiveFailures = 0;
        uint32_t lastPostErrorMillis = 0;
        uint32_t lastPostErrorSuccessMillis = 0;

        uint32_t wifiConnectAttempts = 0;
        uint32_t wifiReconnectEvents = 0;
        uint32_t wifiLastAttemptMillis = 0;
        uint32_t wifiLastConnectedMillis = 0;
        uint32_t wifiLastDisconnectedMillis = 0;
        uint32_t wifiCurrentBackoffMillis = 0;
        uint32_t wifiCurrentAttemptNumber = 0;
    };

    MetricsData gMetrics;
    portMUX_TYPE gMetricsMux = portMUX_INITIALIZER_UNLOCKED;
}

void Metrics::recordSensorRead(bool success, float temperatureC, float humidityPct)
{
    const uint32_t now = millis();
    portENTER_CRITICAL(&gMetricsMux);
    gMetrics.sensorReadTotal++;
    gMetrics.lastSensorReadMillis = now;
    if (success)
    {
        gMetrics.sensorReadSuccess++;
        gMetrics.sensorReadConsecutiveFailures = 0;
        gMetrics.lastSensorReadSuccessMillis = now;
        gMetrics.lastTemperatureC = temperatureC;
        gMetrics.lastHumidityPct = humidityPct;
    }
    else
    {
        gMetrics.sensorReadFailed++;
        gMetrics.sensorReadConsecutiveFailures++;
    }
    portEXIT_CRITICAL(&gMetricsMux);
}

void Metrics::recordPostResult(PostKind kind, bool success)
{
    const uint32_t now = millis();
    portENTER_CRITICAL(&gMetricsMux);
    if (kind == PostKind::Reading)
    {
        gMetrics.postReadingTotal++;
        gMetrics.lastPostReadingMillis = now;
        if (success)
        {
            gMetrics.postReadingConsecutiveFailures = 0;
            gMetrics.lastPostReadingSuccessMillis = now;
        }
        else
        {
            gMetrics.postReadingFailed++;
            gMetrics.postReadingConsecutiveFailures++;
        }
    }
    else
    {
        gMetrics.postErrorTotal++;
        gMetrics.lastPostErrorMillis = now;
        if (success)
        {
            gMetrics.postErrorConsecutiveFailures = 0;
            gMetrics.lastPostErrorSuccessMillis = now;
        }
        else
        {
            gMetrics.postErrorFailed++;
            gMetrics.postErrorConsecutiveFailures++;
        }
    }
    portEXIT_CRITICAL(&gMetricsMux);
}

MetricsSnapshot Metrics::snapshot()
{
    MetricsSnapshot snap{};
    portENTER_CRITICAL(&gMetricsMux);
    snap.sensorReadTotal = gMetrics.sensorReadTotal;
    snap.sensorReadSuccess = gMetrics.sensorReadSuccess;
    snap.sensorReadFailed = gMetrics.sensorReadFailed;
    snap.sensorReadConsecutiveFailures = gMetrics.sensorReadConsecutiveFailures;
    snap.lastSensorReadMillis = gMetrics.lastSensorReadMillis;
    snap.lastSensorReadSuccessMillis = gMetrics.lastSensorReadSuccessMillis;
    snap.lastTemperatureC = gMetrics.lastTemperatureC;
    snap.lastHumidityPct = gMetrics.lastHumidityPct;

    snap.postReadingTotal = gMetrics.postReadingTotal;
    snap.postReadingFailed = gMetrics.postReadingFailed;
    snap.postReadingConsecutiveFailures = gMetrics.postReadingConsecutiveFailures;
    snap.lastPostReadingMillis = gMetrics.lastPostReadingMillis;
    snap.lastPostReadingSuccessMillis = gMetrics.lastPostReadingSuccessMillis;

    snap.postErrorTotal = gMetrics.postErrorTotal;
    snap.postErrorFailed = gMetrics.postErrorFailed;
    snap.postErrorConsecutiveFailures = gMetrics.postErrorConsecutiveFailures;
    snap.lastPostErrorMillis = gMetrics.lastPostErrorMillis;
    snap.lastPostErrorSuccessMillis = gMetrics.lastPostErrorSuccessMillis;

    snap.wifiConnectAttempts = gMetrics.wifiConnectAttempts;
    snap.wifiReconnectEvents = gMetrics.wifiReconnectEvents;
    snap.wifiLastAttemptMillis = gMetrics.wifiLastAttemptMillis;
    snap.wifiLastConnectedMillis = gMetrics.wifiLastConnectedMillis;
    snap.wifiLastDisconnectedMillis = gMetrics.wifiLastDisconnectedMillis;
    snap.wifiCurrentBackoffMillis = gMetrics.wifiCurrentBackoffMillis;
    snap.wifiCurrentAttemptNumber = gMetrics.wifiCurrentAttemptNumber;
    portEXIT_CRITICAL(&gMetricsMux);

    snap.uptimeMillis = millis();
    snap.heapFreeBytes = ESP.getFreeHeap();
    snap.heapMinBytes = ESP.getMinFreeHeap();
    const wl_status_t status = WiFi.status();
    snap.wifiConnected = (status == WL_CONNECTED);
    snap.wifiRssiDbm = snap.wifiConnected ? WiFi.RSSI() : -127;

    if (snap.wifiConnected && snap.wifiLastConnectedMillis != 0)
    {
        if (snap.uptimeMillis >= snap.wifiLastConnectedMillis)
        {
            snap.wifiConnectionDurationMillis = snap.uptimeMillis - snap.wifiLastConnectedMillis;
        }
        else
        {
            snap.wifiConnectionDurationMillis = 0;
        }
    }
    else
    {
        snap.wifiConnectionDurationMillis = 0;
    }

    return snap;
}

void Metrics::recordWifiAttempt(uint32_t attemptNumber, uint32_t backoffMs)
{
    const uint32_t now = millis();
    portENTER_CRITICAL(&gMetricsMux);
    gMetrics.wifiConnectAttempts++;
    gMetrics.wifiCurrentAttemptNumber = attemptNumber;
    gMetrics.wifiLastAttemptMillis = now;
    gMetrics.wifiCurrentBackoffMillis = backoffMs;
    portEXIT_CRITICAL(&gMetricsMux);
}

void Metrics::recordWifiConnected()
{
    const uint32_t now = millis();
    portENTER_CRITICAL(&gMetricsMux);
    gMetrics.wifiLastConnectedMillis = now;
    gMetrics.wifiCurrentBackoffMillis = 0;
    gMetrics.wifiCurrentAttemptNumber = 0;
    portEXIT_CRITICAL(&gMetricsMux);
}

void Metrics::recordWifiDisconnected()
{
    const uint32_t now = millis();
    portENTER_CRITICAL(&gMetricsMux);
    gMetrics.wifiReconnectEvents++;
    gMetrics.wifiLastDisconnectedMillis = now;
    portEXIT_CRITICAL(&gMetricsMux);
}
