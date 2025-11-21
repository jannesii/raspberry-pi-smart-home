#include <HTTPClient.h>
#include <ArduinoJson.h>

#include "io/PosterTask.h"
#include "io/measurements.h"
#include "core/TimeKeeper.h"

PosterTask::PosterTask(ShellyHandler &shelly,
                       LogManager &logManager)
    : shelly_(shelly),
      logger_(logManager)
{
}

void PosterTask::start(uint32_t stackSize, UBaseType_t priority)
{
    // Initialize lastInDeadzone_ before starting
    if (handle_ != nullptr)
    {
        Serial.println("[HeaterTask] Warning: Heater task already running");
        log("Warning: Heater task already running");
        return;
    }
    xTaskCreate(
        &PosterTask::taskEntry,
        "HeaterTask",
        stackSize,
        this, // pass this as pvParameters
        priority,
        &handle_);

    Serial.println("[HeaterTask] Started heater task");
}

void PosterTask::taskEntry(void *pvParameters)
{
    auto *self = static_cast<PosterTask *>(pvParameters);
    self->run();
    // never returns
}

void PosterTask::run()
{
    const TickType_t periodTicks = pdMS_TO_TICKS(taskDelayS_ * 1000);
    TickType_t lastWakeTime = xTaskGetTickCount();
    for (;;)
    {
        String body = "";
        bool isShellyOn = false;
        if (!shelly_.getStatus(isShellyOn, false, &body))
            log("Warning: Failed to get Shelly status");

        float currentTemp = takeMeasurement(false).temperature;

        JsonDocument doc;
        doc["shelly"] = body;
        doc["temperature"] = currentTemp;
        doc["timestamp"] = timekeeper::formatLocal();

        String apiPayload;
        serializeJson(doc, apiPayload);

        Serial.println("=== API payload ===");
        Serial.println(apiPayload);
        Serial.println("===================");

        // --- measure POST time ---
        uint32_t startMs = millis();

        HTTPClient http;
        http.begin(apiURL_);
        http.addHeader("Content-Type", "application/json");
        int code = http.POST(apiPayload);
        http.end();

        uint32_t durationMs = millis() - startMs;
        // -------------------------

        Serial.printf("Posted to API, response code: %d, took %lu ms\n",
                      code, static_cast<unsigned long>(durationMs));

        // update running average (since boot)
        postCount_++;
        avgPostMs_ += (static_cast<float>(durationMs) - avgPostMs_) / postCount_;

        // occasionally print the average
        if (postCount_ % 10 == 0) {
            Serial.printf("Average POST time over %lu posts: %.1f ms\n",
                          static_cast<unsigned long>(postCount_), avgPostMs_);
        }

        // Wait until the next exact period boundary
        vTaskDelayUntil(&lastWakeTime, periodTicks);
    }
}


// ---------------- helpers ----------------

String PosterTask::log(const String &msg) const
{
    String line;
    line.reserve(60 + msg.length());
    line += timekeeper::formatLocal();
    line += " [HeaterTask] ";
    line += msg;
    logger_.append(line);
    return line;
}