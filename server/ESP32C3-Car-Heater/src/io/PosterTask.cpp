#include <ArduinoJson.h>
#include <WiFi.h>

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

        /* Serial.println("=== API payload ===");
        Serial.println(apiPayload);
        Serial.println("==================="); */

        // Fine-grained timing around the HTTP POST
        uint32_t t0 = millis();

        // Log current Wi-Fi RSSI to correlate latency with signal quality
        int32_t rssi = WiFi.RSSI();
        Serial.printf("[WiFi] RSSI before POST: %ld dBm\n", (long)rssi);

        // Init persistent HTTP client (once)
        initHttpIfNeeded();
        uint32_t t1 = millis();

        // (Re)configure this request
        http_.begin(apiURL_);
        http_.addHeader("Content-Type", "application/json");
        http_.addHeader("x-api-key", apiKey_);
        uint32_t t2 = millis();

        int code = http_.POST(apiPayload);
        uint32_t t3 = millis();

        String respBody;
        uint32_t t4 = t3; // will be updated if we read/process response

        if (code > 0) {
            Serial.printf("HTTP POST response code: %d\n", code);
            respBody = http_.getString();
            Serial.println("Response body:");
            Serial.println(respBody);

            processServerCommands(respBody);   // may queue action_results / logs
            t4 = millis();
        } else {
            Serial.printf("HTTP POST failed, error: %d\n", code);
        }

        // Close this HTTP transaction. With setReuse(true) and
        // a keep-alive-capable server, the underlying TCP/TLS
        // connection can be reused by the same HTTPClient.
        http_.end();
        uint32_t t5 = millis();

        Serial.printf(
            "HTTP timing: init=%lu ms, setup=%lu ms, post=%lu ms, resp/cmd=%lu ms, end=%lu ms, total=%lu ms\n",
            static_cast<unsigned long>(t1 - t0),
            static_cast<unsigned long>(t2 - t1),
            static_cast<unsigned long>(t3 - t2),
            static_cast<unsigned long>(t4 - t3),
            static_cast<unsigned long>(t5 - t4),
            static_cast<unsigned long>(t5 - t0));

        uint32_t durationMs = t5 - t0;

        Serial.printf("Posted to API, response code: %d, took %lu ms\n",
                      code, static_cast<unsigned long>(durationMs));

        postCount_++;
        avgPostMs_ += (static_cast<float>(durationMs) - avgPostMs_) / postCount_;

        if (postCount_ % 10 == 0) {
            Serial.printf("Average POST time over %lu posts: %.1f ms\n",
                          static_cast<unsigned long>(postCount_), avgPostMs_);
        }

        // If commands/logs from this response produced pending results,
        // send them in a separate immediate POST using the same persistent client.
        if (code > 0) {
            sendImmediateResultIfNeeded();
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

    // If time is not valid, or we don't care about aligned slots,
    // just do a plain fixed delay.
    if (!timekeeper::isValid()) {
        vTaskDelay(pdMS_TO_TICKS(taskDelayS_ * 1000));
        return;
    }

    const uint32_t interval = static_cast<uint32_t>(taskDelayS_);  // seconds

    // Keep existing behavior for other values: only align when using 5s or 10s.
    if (interval != 5U && interval != 10U) {
        vTaskDelay(pdMS_TO_TICKS(interval * 1000));
        return;
    }

    time_t now = timekeeper::nowEpochSeconds();
    if (now <= 0) {
        vTaskDelay(pdMS_TO_TICKS(interval * 1000));
        return;
    }

    const uint32_t offset   = static_cast<uint32_t>(now % interval);

    // We want posts at epoch times that are multiples of `interval`, e.g.
    // interval=10 -> ... :40, :50, :00, ...
    // interval=5  -> ... :35, :40, :45, ...
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

// After processing commands from the server, use this to send their
// results/logs back immediately (in a second POST), rather than
// waiting for the next scheduled slot.
void PosterTask::sendImmediateResultIfNeeded()
{
    if (pendingActionCount_ == 0 && pendingLogs_.length() == 0) {
        return; // nothing to send
    }

    // Build a fresh status payload including the new action_results/logs.
    String body = "";
    bool isShellyOn = false;
    bool shellySuccess = shelly_.getStatus(isShellyOn, false, &body);
    float currentTemp = takeMeasurement(false).temperature;

    JsonDocument doc;
    if (shellySuccess)
        doc["shelly"] = body;
    else
        doc["shelly_connected"] = false;
    doc["temperature"] = currentTemp;
    doc["timestamp"]  = timekeeper::formatLocal();

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
        pendingActionCount_ = 0;
    }

    if (pendingLogs_.length() > 0) {
        doc["logs"] = pendingLogs_;
        pendingLogs_.clear();
    }

    String apiPayload;
    serializeJson(doc, apiPayload);

    Serial.println("=== Immediate API payload ===");
    Serial.println(apiPayload);
    Serial.println("=============================");

    uint32_t t0 = millis();

    // Reuse the same persistent HTTP client
    initHttpIfNeeded();
    uint32_t t1 = millis();

    http_.begin(apiURL_);
    http_.addHeader("Content-Type", "application/json");
    http_.addHeader("x-api-key", apiKey_);
    uint32_t t2 = millis();

    int code = http_.POST(apiPayload);
    uint32_t t3 = millis();

    if (code > 0) {
        Serial.printf("Immediate HTTP POST response code: %d\n", code);
        String respBody = http_.getString();
        uint32_t t4 = millis();
        Serial.println("Immediate response body:");
        Serial.println(respBody);
        // IMPORTANT: don't process more commands here to avoid chains;
        // any new commands will be processed on the next scheduled cycle.
        http_.end();
        uint32_t t5 = millis();

        Serial.printf(
            "Immediate HTTP timing: init=%lu ms, setup=%lu ms, post=%lu ms, read=%lu ms, end=%lu ms, total=%lu ms\n",
            static_cast<unsigned long>(t1 - t0),
            static_cast<unsigned long>(t2 - t1),
            static_cast<unsigned long>(t3 - t2),
            static_cast<unsigned long>(t4 - t3),
            static_cast<unsigned long>(t5 - t4),
            static_cast<unsigned long>(t5 - t0));
    } else {
        Serial.printf("Immediate HTTP POST failed, error: %d\n", code);
        http_.end();
        uint32_t t5 = millis();
        Serial.printf(
            "Immediate HTTP timing (fail): init=%lu ms, setup=%lu ms, post=%lu ms, total=%lu ms\n",
            static_cast<unsigned long>(t1 - t0),
            static_cast<unsigned long>(t2 - t1),
            static_cast<unsigned long>(t3 - t2),
            static_cast<unsigned long>(t5 - t0));
    }

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

void PosterTask::handlePostDelay(uint32_t seconds)
{
    if (seconds == 0) {
        Serial.println("[CMD] post_delay with invalid value: 0");
        queueActionResult("post_delay", false, "delay must be > 0");
        return;
    }

    // Optionally clamp to a sane max (e.g. 1 hour)
    const uint32_t maxDelay = 3600;
    if (seconds > maxDelay) {
        Serial.printf("[CMD] post_delay too large: %lu (clamping to %lu)\n",
                      static_cast<unsigned long>(seconds),
                      static_cast<unsigned long>(maxDelay));
        seconds = maxDelay;
    }

    taskDelayS_ = seconds;

    Serial.printf("[CMD] post_delay set to %lu seconds\n",
                  static_cast<unsigned long>(taskDelayS_));

    String note = "delay set to ";
    note += String(taskDelayS_);
    note += "s";
    queueActionResult("post_delay", true, note);
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
        // Some commands (like post_delay) may carry additional parameters

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
        } else if (strcmp(action, "post_delay") == 0) {
            uint32_t delaySeconds = cmd["delay"] | 0;
            handlePostDelay(delaySeconds);
        } else {
            handleUnknownAction(action);
            queueActionResult(action, false, "unknown action");
        }
    }
}

void PosterTask::initHttpIfNeeded()
{
    if (!httpInitialized_) {
        // Called once; HTTPClient object is persistent
        http_.setTimeout(5000);    // or whatever you prefer
        http_.setReuse(true);      // allow keep-alive / reuse if server supports it
        httpInitialized_ = true;
    }
}
