#include <HTTPClient.h>
#include <ArduinoJson.h>

#include "heating/PosterTask.h"
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
    for (;;)
    {
        String body = "";
        bool isShellyOn = false;
        if (!shelly_.getStatus(isShellyOn, false, &body))
            log("Warning: Failed to get Shelly status");

        // Serial.printf("Body: %s\n", body.c_str());

        float currentTemp = takeMeasurement(false).temperature;

        JsonDocument doc;
        doc["shelly"] = body;
        doc["temperature"] = currentTemp;

        String apiPayload;
        serializeJson(doc, apiPayload);

        Serial.println("=== API payload ===");
        Serial.println(apiPayload);
        Serial.println("===================");

        HTTPClient http;
        http.begin(apiURL_);
        http.addHeader("Content-Type", "application/json");

        int code = http.POST(apiPayload);
        http.end();
        Serial.printf("Posted to API, response code: %d\n", code);


        vTaskDelay(pdMS_TO_TICKS(taskDelayS_ * 1000));
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