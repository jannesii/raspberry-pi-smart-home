#include "io/WebSocketTask.h"
#include "core/TimeKeeper.h"
#include "io/measurements.h"
#include <core/staticconfig.h>
#include <ArduinoJson.h>
#include <WiFi.h>

// Static instance pointer for WebSocket callback
WebSocketTask* WebSocketTask::instance_ = nullptr;

WebSocketTask::WebSocketTask(ShellyHandler &shelly, LogManager &logManager)
    : shelly_(shelly), logger_(logManager)
{
    instance_ = this;
    memset(recentCommands_, 0, sizeof(recentCommands_));
}

void WebSocketTask::start(uint32_t stackSize, UBaseType_t priority)
{
    if (handle_ != nullptr)
    {
        Serial.println("[WebSocket] Warning: Task already running");
        return;
    }

    running_ = true;
    xTaskCreate(
        &WebSocketTask::taskEntry,
        "WebSocketTask",
        stackSize,
        this,
        priority,
        &handle_);

    Serial.println("[WebSocket] Task started");
}

void WebSocketTask::stop()
{
    running_ = false;
    if (handle_ != nullptr)
    {
        disconnect();
        vTaskDelay(pdMS_TO_TICKS(100));
        vTaskDelete(handle_);
        handle_ = nullptr;
        log("Task stopped");
    }
}

void WebSocketTask::taskEntry(void *pvParameters)
{
    auto *self = static_cast<WebSocketTask *>(pvParameters);
    self->run();
}

void WebSocketTask::run()
{
    // Wait for WiFi to be connected
    while (running_ && WiFi.status() != WL_CONNECTED)
    {
        Serial.println("[WebSocket] Waiting for WiFi...");
        vTaskDelay(pdMS_TO_TICKS(1000));
    }

    // Initial connection
    connect();

    while (running_)
    {
        // Let WebSocket library process events
        webSocket_.loop();

        uint32_t now = millis();

        // Send heartbeat to keep connection alive
        if (connected_ && authenticated_ && (now - lastHeartbeatMs_ >= HEARTBEAT_INTERVAL_MS))
        {
            sendHeartbeat();
            lastHeartbeatMs_ = now;
        }

        // Send pending status update
        if (connected_ && authenticated_ && statusUpdatePending_)
        {
            sendStatus();
            statusUpdatePending_ = false;
        }

        // Small delay to prevent tight loop
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    disconnect();
}

// Static WebSocket event handler (bridges to instance method)
void WebSocketTask::webSocketEventStatic(WStype_t type, uint8_t *payload, size_t length)
{
    if (instance_ != nullptr)
    {
        instance_->onWebSocketEvent(type, payload, length);
    }
}

void WebSocketTask::onWebSocketEvent(WStype_t type, uint8_t *payload, size_t length)
{
    switch (type)
    {
    case WStype_DISCONNECTED:
        Serial.println("[WebSocket] Disconnected");
        connected_ = false;
        authenticated_ = false;
        stats_.disconnectCount++;
        if (running_ && WiFi.status() == WL_CONNECTED)
        {
            const uint32_t nextDelayMs = reconnectDelayMs_;
            webSocket_.setReconnectInterval(nextDelayMs);
            reconnectDelayMs_ = min(reconnectDelayMs_ * 2, RECONNECT_DELAY_MAX_MS);
            Serial.printf("[WebSocket] Next reconnect attempt in %lu ms\n",
                          static_cast<unsigned long>(nextDelayMs));
        }
        break;

    case WStype_CONNECTED:
        Serial.printf("[WebSocket] Connected to %s%s\n", WS_HOST, WS_PATH);
        connected_ = true;
        stats_.connectCount++;
        stats_.lastConnectedMs = millis();
        
        // Reset backoff on successful connection
        reconnectDelayMs_ = RECONNECT_DELAY_MIN_MS;
        webSocket_.setReconnectInterval(reconnectDelayMs_);
        
        // Send authentication immediately
        sendAuthentication();
        break;

    case WStype_TEXT:
        stats_.messagesReceived++;
        processMessage(reinterpret_cast<const char *>(payload), length);
        break;

    case WStype_BIN:
        Serial.printf("[WebSocket] Received binary data (%zu bytes), ignoring\n", length);
        break;

    case WStype_PING:
        Serial.println("[WebSocket] Received PING");
        break;

    case WStype_PONG:
        Serial.println("[WebSocket] Received PONG");
        break;

    case WStype_ERROR:
        Serial.printf("[WebSocket] Error: %s\n", payload ? reinterpret_cast<const char *>(payload) : "unknown");
        break;

    default:
        Serial.printf("[WebSocket] Unhandled event type: %d\n", static_cast<int>(type));
        break;
    }
}

void WebSocketTask::connect()
{
    Serial.printf("[WebSocket] Connecting to wss://%s:%d%s\n", WS_HOST, WS_PORT, WS_PATH);

    // Configure WebSocket with SSL
    webSocket_.beginSSL(WS_HOST, WS_PORT, WS_PATH);
    webSocket_.onEvent(webSocketEventStatic);
    
    // Let the library reconnect, but control its retry interval ourselves.
    webSocket_.setReconnectInterval(reconnectDelayMs_);
    
    // Enable heartbeat (WebSocket ping/pong)
    webSocket_.enableHeartbeat(15000, 3000, 2);  // ping every 15s, timeout 3s, 2 retries
}

void WebSocketTask::disconnect()
{
    webSocket_.disconnect();
    connected_ = false;
    authenticated_ = false;
}

void WebSocketTask::sendAuthentication()
{
    JsonDocument doc;
    doc["auth"] = WS_API_KEY;  // From staticconfig.h
    doc["device_id"] = "car_heater_esp32";
    doc["firmware_version"] = "2.0.0";

    String json;
    serializeJson(doc, json);

    Serial.println("[WebSocket] Sending authentication...");
    webSocket_.sendTXT(json);
    stats_.messagesSent++;
}

void WebSocketTask::processMessage(const char *payload, size_t length)
{
    if (length == 0 || payload == nullptr)
        return;

    Serial.printf("[WebSocket] Received: %.*s\n", static_cast<int>(min(length, (size_t)200)), payload);

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err)
    {
        Serial.printf("[WebSocket] JSON parse error: %s\n", err.c_str());
        return;
    }

    // Handle authentication response
    if (doc["status"].is<const char*>())
    {
        const char *status = doc["status"];
        if (strcmp(status, "authenticated") == 0)
        {
            Serial.println("[WebSocket] ✓ Authenticated successfully");
            authenticated_ = true;
            statusUpdatePending_ = true;
            return;
        }
    }

    // Handle error response
    if (doc["error"].is<const char*>())
    {
        const char *error = doc["error"];
        Serial.printf("[WebSocket] Server error: %s\n", error);
        
        if (strcmp(error, "unauthorized") == 0)
        {
            Serial.println("[WebSocket] Authentication failed - check API key");
            // Don't try to reconnect immediately with bad credentials
            reconnectDelayMs_ = RECONNECT_DELAY_MAX_MS;
        }
        return;
    }

    // Handle command array (same format as HTTP response)
    if (doc.is<JsonArray>())
    {
        processCommands(payload, length);
        return;
    }

    Serial.println("[WebSocket] Unknown message format");
}

void WebSocketTask::processCommands(const char *jsonArray, size_t length)
{
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, jsonArray, length);
    if (err || !doc.is<JsonArray>())
    {
        Serial.println("[WebSocket] Invalid command array");
        return;
    }

    JsonArray arr = doc.as<JsonArray>();
    Serial.printf("[WebSocket] Processing %u commands\n", static_cast<unsigned>(arr.size()));

    for (JsonObject cmd : arr)
    {
        const char *action = cmd["action"] | "";
        const char *source = cmd["source"] | "websocket";

        if (strlen(action) == 0)
            continue;

        Serial.printf("[WebSocket] Executing command: %s (from %s)\n", action, source);
        executeCommand(action, source);
    }
}

void WebSocketTask::executeCommand(const char *action, const char *source)
{
    bool success = false;
    const char *note = nullptr;
    bool sendImmediateStatus = false;

    if (strcmp(action, "turn_on") == 0)
    {
        handleTurnOn();
        success = true;
        sendImmediateStatus = true;
    }
    else if (strcmp(action, "turn_off") == 0)
    {
        handleTurnOff();
        success = true;
        sendImmediateStatus = true;
    }
    else if (strcmp(action, "get_logs") == 0)
    {
        handleGetLogs();
        success = true;
        sendImmediateStatus = true;
    }
    else if (strcmp(action, "esp_restart") == 0)
    {
        handleEspRestart();
        success = true;
        note = "restarting";
    }
    else if (strcmp(action, "shelly_restart") == 0)
    {
        handleShellyReboot();
        success = true;
    }
    else
    {
        Serial.printf("[WebSocket] Unknown command: %s\n", action);
        note = "unknown command";
    }

    // Record for deduplication
    recordCommandExecution(action);
    stats_.commandsExecuted++;

    // Send result back to server
    sendActionResult(action, success, note);
    if (success && sendImmediateStatus)
    {
        Serial.printf("[WebSocket] Sending immediate status update after %s\n", action);
        sendStatus();
    }
}

void WebSocketTask::handleTurnOn()
{
    Serial.println("[WebSocket] CMD: turn_on");
    log("WS CMD: turn_on");
    shelly_.switchOn();
}

void WebSocketTask::handleTurnOff()
{
    Serial.println("[WebSocket] CMD: turn_off");
    log("WS CMD: turn_off");
    shelly_.switchOff();
}

void WebSocketTask::handleGetLogs()
{
    Serial.println("[WebSocket] CMD: get_logs");

    // Send logs in the next status update
    logsRequested_ = true;
    statusUpdatePending_ = true;
}

void WebSocketTask::handleEspRestart()
{
    Serial.println("[WebSocket] CMD: esp_restart");
    log("WS CMD: esp_restart - restarting in 1s");
    
    // Send result before restart
    sendActionResult("esp_restart", true, "restarting now");
    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
}

void WebSocketTask::handleShellyReboot()
{
    Serial.println("[WebSocket] CMD: shelly_restart");
    log("WS CMD: shelly_restart");
    shelly_.reboot();
}

void WebSocketTask::sendStatus()
{
    if (!connected_ || !authenticated_)
        return;

    String json = buildStatusJson();
    
    Serial.println("[WebSocket] Sending status update");
    webSocket_.sendTXT(json);
    stats_.messagesSent++;
}

void WebSocketTask::sendHeartbeat()
{
    if (!connected_ || !authenticated_)
        return;

    JsonDocument doc;
    doc["type"] = "heartbeat";
    doc["timestamp"] = timekeeper::formatLocal();
    doc["uptime_ms"] = millis();

    String json;
    serializeJson(doc, json);

    webSocket_.sendTXT(json);
    stats_.messagesSent++;
}

void WebSocketTask::sendActionResult(const char *action, bool success, const char *note)
{
    if (!connected_ || !authenticated_)
        return;

    JsonDocument doc;
    JsonArray results = doc["action_results"].to<JsonArray>();
    JsonObject r = results.add<JsonObject>();
    r["action"] = action;
    r["success"] = success;
    if (note != nullptr)
        r["note"] = note;
    doc["timestamp"] = timekeeper::formatLocal();

    String json;
    serializeJson(doc, json);

    Serial.printf("[WebSocket] Sending action result: %s = %s\n", action, success ? "success" : "failed");
    webSocket_.sendTXT(json);
    stats_.messagesSent++;
}

String WebSocketTask::buildStatusJson()
{
    // Get Shelly status
    String shellyBody;
    bool isOn = false;
    bool shellyOk = shelly_.getStatus(isOn, false, &shellyBody);

    // Get temperature
    float temp = takeMeasurement(false).temperature;

    JsonDocument doc;
    doc["timestamp"] = timekeeper::formatLocal();
    doc["temperature"] = temp;

    if (shellyOk)
        doc["shelly"] = shellyBody;
    else
        doc["shelly_connected"] = false;

    // Only include logs when the server explicitly asks for them.
    if (logsRequested_)
    {
        String logs = logger_.toStringNewestFirst();
        if (logs.length() > 0 && logs.length() < 2000)
        {
            doc["logs"] = logs;
        }
        logsRequested_ = false;
    }

    // Include stats
    JsonObject statsObj = doc["ws_stats"].to<JsonObject>();
    statsObj["connected"] = connected_;
    statsObj["uptime_ms"] = millis() - stats_.lastConnectedMs;
    statsObj["messages_sent"] = stats_.messagesSent;
    statsObj["messages_received"] = stats_.messagesReceived;
    statsObj["commands_executed"] = stats_.commandsExecuted;

    String json;
    serializeJson(doc, json);
    return json;
}

void WebSocketTask::queueStatusUpdate()
{
    statusUpdatePending_ = true;
}

void WebSocketTask::recordCommandExecution(const char *action)
{
    RecentCommand &slot = recentCommands_[recentCommandIndex_];
    strncpy(slot.action, action, sizeof(slot.action) - 1);
    slot.action[sizeof(slot.action) - 1] = '\0';
    slot.executedAtMs = millis();

    recentCommandIndex_ = (recentCommandIndex_ + 1) % MAX_RECENT_COMMANDS;
}

bool WebSocketTask::wasCommandExecutedRecently(const char *action, uint32_t withinMs)
{
    uint32_t now = millis();
    
    for (size_t i = 0; i < MAX_RECENT_COMMANDS; ++i)
    {
        if (recentCommands_[i].executedAtMs == 0)
            continue;
            
        if ((now - recentCommands_[i].executedAtMs) <= withinMs)
        {
            if (strcmp(recentCommands_[i].action, action) == 0)
            {
                return true;
            }
        }
    }
    return false;
}

WebSocketTask::Stats WebSocketTask::getStats() const
{
    Stats s = stats_;
    if (connected_)
    {
        s.uptimeMs = millis() - stats_.lastConnectedMs;
    }
    return s;
}

String WebSocketTask::log(const String &msg) const
{
    String line;
    line.reserve(60 + msg.length());
    line += timekeeper::formatLocal();
    line += " [WebSocket] ";
    line += msg;
    logger_.append(line);
    return line;
}
