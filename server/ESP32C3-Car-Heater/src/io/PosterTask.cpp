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
    for (;;)
    {
        String body = "";
        bool isShellyOn = false;
        bool shellySuccess = shelly_.getStatus(isShellyOn, false, &body);

        float currentTemp = takeMeasurement(false).temperature;

        if (WiFi.status() != WL_CONNECTED) {
            wifiDisconnectCount_++;

            Serial.printf(
                "WiFi not connected (count=%lu), skipping POST\n",
                static_cast<unsigned long>(wifiDisconnectCount_)
            );

            if (wifiDisconnectCount_ >= WIFI_MAX_DISCONNECT_LOOPS) {
                Serial.println("WiFi has been down for too long, restarting ESP...");
                log("WiFi has been down for too long, restarting ESP...");
                vTaskDelay(pdMS_TO_TICKS(1000)); // let logs flush
                esp_restart();                   // or ESP.restart();
            }

            sleepUntilNextSlot();
            continue;
        }

        // Wi-Fi is OK: reset the counter
        wifiDisconnectCount_ = 0;


        JsonDocument doc;
        if (shellySuccess)
            doc["shelly"] = body;
        else
            doc["shelly_connected"] = false;
        doc["temperature"] = currentTemp;
        doc["timestamp"]  = timekeeper::formatLocal();

        // ---- attach pending action results (from previous loop) ----
        if (pendingActionCount_ > 0) {
            JsonArray results = doc["action_results"].to<JsonArray>();
            for (size_t i = 0; i < pendingActionCount_; ++i) {
                JsonObject r = results.add<JsonObject>();
                String action = pendingActions_[i].action;
                if (action == "esp_restart") 
                    espRestartResultSent_ = true;
                r["action"]  = action;
                r["success"] = pendingActions_[i].success;
                if (pendingActions_[i].note.length() > 0) {
                    r["note"] = pendingActions_[i].note;
                }
            }
            // clear after sending
            pendingActionCount_ = 0;
        }

        // ---- attach logs (from previous handleGetLogs) ----
        if (pendingLogs_.length() > 0) {
            doc["logs"] = pendingLogs_;
            pendingLogs_.clear();
        }
        // ----------------------------------------------------

        String apiPayload;
        serializeJson(doc, apiPayload);

        Serial.println("=== API payload ===");
        Serial.println(apiPayload);
        Serial.println("===================");

        uint32_t startMs = millis();

        HTTPClient http;
        http.setTimeout(5000);
        http.begin(apiURL_);
        http.addHeader("Content-Type", "application/json");
        http.addHeader("x-api-key", apiKey_);

        int code = http.POST(apiPayload);

        String respBody;

        if (code > 0) {
            Serial.printf("HTTP POST response code: %d\n", code);
            respBody = http.getString();
            Serial.println("Response body:");
            Serial.println(respBody);
            processServerCommands(respBody);   // this will queue results for *next* post
        } else {
            Serial.printf("HTTP POST failed, error: %d\n", code);
        }

        http.end();

        uint32_t durationMs = millis() - startMs;

        Serial.printf("Posted to API, response code: %d, took %lu ms\n",
                      code, static_cast<unsigned long>(durationMs));

        postCount_++;
        avgPostMs_ += (static_cast<float>(durationMs) - avgPostMs_) / postCount_;

        if (postCount_ % 10 == 0) {
            Serial.printf("Average POST time over %lu posts: %.1f ms\n",
                          static_cast<unsigned long>(postCount_), avgPostMs_);
        }

        if (espRestartPending_ && espRestartResultSent_) 
        {
            Serial.println("ESP restart requested, restarting now...");
            log("ESP restart requested, restarting now...");
            vTaskDelay(pdMS_TO_TICKS(1000)); // wait a bit for logs to flush
            esp_restart();
        }

        sleepUntilNextSlot();
    }
}


void PosterTask::sleepUntilNextSlot()
{
    // Fallback: if delay is zero, don't sleep at all
    if (taskDelayS_ == 0) {
        return;
    }

    // If time is not valid, or we don't care about 10-second alignment,
    // just do a plain fixed delay.
    if (!timekeeper::isValid() || (taskDelayS_ % 10) != 0) {
        vTaskDelay(pdMS_TO_TICKS(taskDelayS_ * 1000));
        return;
    }

    time_t now = timekeeper::nowEpochSeconds();
    if (now <= 0) {
        vTaskDelay(pdMS_TO_TICKS(taskDelayS_ * 1000));
        return;
    }

    const uint32_t interval = static_cast<uint32_t>(taskDelayS_);  // seconds
    const uint32_t offset   = static_cast<uint32_t>(now % interval);

    // We want posts at epoch times that are multiples of `interval`, e.g.
    // interval=10 -> ... :40, :50, :00, ...
    uint32_t secondsToNext = (offset == 0) ? interval : (interval - offset);

    // Optional: debug
    // Serial.printf("sleepUntilNextSlot: now=%ld, offset=%lu, wait=%lu s\n",
    //               (long)now,
    //               (unsigned long)offset,
    //               (unsigned long)secondsToNext);

    vTaskDelay(pdMS_TO_TICKS(secondsToNext * 1000));
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

void PosterTask::queueActionResult(const char *action, bool success, const String &note)
{
    if (!action) return;

    if (pendingActionCount_ >= kMaxPendingActions) {
        // Optional: overwrite the last one instead of dropping
        // pendingActions_[kMaxPendingActions - 1] = {...}
        return;
    }

    ActionResult &slot = pendingActions_[pendingActionCount_++];
    slot.action = action;
    slot.success = success;
    slot.note = note;
}

// Placeholders for your real implementations
void PosterTask::handleTurnOn()
{
    Serial.println("[CMD] turn_on");
    bool success = shelly_.switchOn();
    queueActionResult("turn_on", success, success ? "" : "shelly.switchOn() failed");
}

void PosterTask::handleTurnOff()
{
    Serial.println("[CMD] turn_off");
    bool success = shelly_.switchOff();
    queueActionResult("turn_off", success, success ? "" : "shelly.switchOff() failed");
}

void PosterTask::handleGetLogs()
{
    Serial.println("[CMD] get_logs");
    // Capture logs to send on NEXT POST
    pendingLogs_ = logger_.toStringNewestFirst();
    queueActionResult("get_logs", true, "");
}

void PosterTask::handleEspRestart()
{
    Serial.println("[CMD] esp_restart");
    espRestartPending_ = true;
    espRestartResultSent_ = false;
    queueActionResult("esp_restart", true, "restarting ESP");
}

void PosterTask::handleShellyReboot()
{
    Serial.println("[CMD] shelly_restart");
    bool success = shelly_.reboot();
    queueActionResult("shelly_restart", success, success ? "" : "shelly.reboot() failed");
}


void PosterTask::handleUnknownAction(const char* action)
{
    Serial.printf("[CMD] unknown action: '%s'\n", action ? action : "(null)");
}

// Main parser: call this with respBody from HTTPClient
void PosterTask::processServerCommands(const String &respBody)
{
    if (respBody.isEmpty()) {
        Serial.println("[CMD] Empty response body, nothing to do");
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, respBody);
    if (err) {
        Serial.print("[CMD] Failed to parse JSON: ");
        Serial.println(err.c_str());
        return;
    }

    if (!doc.is<JsonArray>()) {
        Serial.println("[CMD] Expected JSON array of commands");
        return;
    }

    JsonArray arr = doc.as<JsonArray>();
    Serial.printf("[CMD] Processing %u commands\n", static_cast<unsigned>(arr.size()));

    for (JsonObject cmd : arr) {
        const char *action = cmd["action"] | "";

        if (strcmp(action, "turn_on") == 0) {
            handleTurnOn();
        } else if (strcmp(action, "turn_off") == 0) {
            handleTurnOff();
        } else if (strcmp(action, "get_logs") == 0) {
            handleGetLogs();
        } else if (strcmp(action, "esp_restart") == 0) {
            handleEspRestart();
        } else if (strcmp(action, "shelly_restart") == 0) {
            handleShellyReboot();
        } else {
            Serial.printf("[CMD] unknown action: '%s'\n", action);
            queueActionResult(action, false, "unknown action");
        }
    }
}
