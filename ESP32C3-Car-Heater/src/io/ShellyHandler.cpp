#include "io/ShellyHandler.h"

#include <WiFi.h>
#include <HTTPClient.h>
#include "core/TimeKeeper.h"

#ifndef SHELLY_BOUNDED_SCRIPT_ID
#define SHELLY_BOUNDED_SCRIPT_ID 1
#endif

ShellyHandler::ShellyHandler(String ipAddress)
{
    // Build base URL: "http://192.168.33.1/rpc/Switch.Set?id=0&on="
    ip_ = ipAddress;
    bootId_ = String(esp_random(), HEX) + String(esp_random(), HEX);
    baseUrl_ = String("http://") + ipAddress + "/rpc/Switch.Set?id=0&on=";
    Serial.printf("[Shelly] Initialized with base URL: %s\n", baseUrl_.c_str());
}


bool ShellyHandler::switchOn()
{
    return false; // ON requires identity/lifetime through executeHeaterCommand.
}

bool ShellyHandler::switchOff()
{
    JsonDocument cmd;
    cmd["action"] = "turn_off";
    std::string body; serializeJson(cmd, body);
    bool persisted = postRelay(body);
    bool direct = sendSwitchRequest(false);
    return persisted || direct;
}

bool ShellyHandler::toggle()
{
    bool isOn;
    if (!getStatus(isOn))
    {
        Serial.println("[Shelly] Failed to get current status for toggle");
        return false;
    }
    return isOn ? switchOff() : false;
}

bool ShellyHandler::sendSwitchRequest(bool on)
{
    if (WiFi.status() != WL_CONNECTED)
    {
        Serial.println("[Shelly] WiFi not connected, cannot send request");
        return false;
    }

    String url = baseUrl_ + (on ? "true" : "false");
    Serial.print("[Shelly] Request: ");
    Serial.println(url);

    HTTPClient http;
    http.begin(url);

    int httpCode = http.GET();

    if (httpCode <= 0)
    {
        Serial.print("[Shelly] HTTP GET failed: ");
        Serial.println(http.errorToString(httpCode));
        http.end();
        return false;
    }

    Serial.print("[Shelly] HTTP status: ");
    Serial.println(httpCode);

    // Optional: read response body for debugging
    String payload = http.getString();
    Serial.print("[Shelly] Response: ");
    Serial.println(payload);

    http.end();

    // Treat any 2xx as success
    return (httpCode >= 200 && httpCode < 300);
}

bool ShellyHandler::getStatus(bool &isOn, bool verbose, String *respBody)
{
    if (WiFi.status() != WL_CONNECTED)
    {
        Serial.println("[Shelly] WiFi not connected, cannot query status");
        return false;
    }

    // For Gen3: /rpc/Switch.GetStatus?id=0
    String url = String("http://") + ip_ + "/rpc/Switch.GetStatus?id=0";
    if (verbose) {
        Serial.print("[Shelly] Status request: ");
        Serial.println(url);
    }

    HTTPClient http;
    http.begin(url);

    int httpCode = http.GET();
    if (httpCode <= 0)
    {
        Serial.print("[Shelly] HTTP GET failed (status): ");
        Serial.println(http.errorToString(httpCode));
        http.end();
        return false;
    }

    if (verbose) {
        Serial.print("[Shelly] Status HTTP code: ");
        Serial.println(httpCode);
    }

    if (httpCode < 200 || httpCode >= 300)
    {
        http.end();
        return false;
    }

    String payload = http.getString();
    http.end();

    if (respBody) {
        *respBody = payload;
    }

    if (verbose) {
        Serial.print("[Shelly] Status response: ");
        Serial.println(payload);
    }

    // Very simple parsing: look for output/on true/false
    if (payload.indexOf("\"output\":true") != -1 || payload.indexOf("\"on\":true") != -1)
    {
        isOn = true;
        return true;
    }

    if (payload.indexOf("\"output\":false") != -1 || payload.indexOf("\"on\":false") != -1)
    {
        isOn = false;
        return true;
    }

    Serial.println("[Shelly] Could not parse on/off state from response");
    return false;
}

bool ShellyHandler::reboot()
{
    HTTPClient http;
    String url = "http://" + ip_ + "/rpc/Shelly.Reboot";
    http.begin(url);
    http.addHeader("Content-Type", "application/json");

    int code = http.POST("{}");
    http.end();

    return (code == 200);
}

bool ShellyHandler::ping() {
    HTTPClient http;
    String url = "http://" + ip_ + "/rpc/Shelly.GetStatus";

    http.begin(url);
    int code = http.GET();
    http.end();

    return (code == 200);
}


bool ShellyHandler::executeHeaterCommand(JsonObjectConst command, String* note) {
    std::string resultNote;
    time_t now = timekeeper::nowEpochSeconds();
    bool success = RelayExecutor::execute(command, now, *this, bootId_.c_str(), &resultNote);
    if (note) *note = String(resultNote.c_str());
    if (!success) {
        Serial.printf("[BoundedRelay] action=%s session=%s rejected: %s | now=%lld issued=%.0f expires=%.0f finish=%.0f\n",
            command["action"] | "", command["session_id"] | "", resultNote.c_str(),
            static_cast<long long>(now), command["issued_at"].as<double>(),
            command["expires_at"].as<double>(), command["finish_at"].as<double>());
    }
    if (!success && strcmp(command["action"] | "", "turn_on") == 0) {
        // Unsupported capability/invalid deadline is fail-closed, not successful ON.
        switchOff();
    }
    return success;
}

bool ShellyHandler::postRelay(const std::string& command, std::string* note) {
    if (WiFi.status() != WL_CONNECTED) {
        if (note) *note = "Wi-Fi unavailable";
        return false;
    }
    HTTPClient http;
    String url = "http://" + ip_ + "/script/" + String(SHELLY_BOUNDED_SCRIPT_ID) + "/bounded";
    http.setTimeout(5000);
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    int code = http.POST(String(command.c_str()));
    String body = http.getString();
    JsonDocument response;
    String detail;
    if (!deserializeJson(response, body)) detail = String(response["note"] | "");
    if (detail.length() > 160) detail = detail.substring(0, 160);
    String resultNote = "Shelly HTTP " + String(code);
    if (detail.length()) resultNote += ": " + detail;
    else if (code <= 0) resultNote += ": " + http.errorToString(code);
    if (code != 200 && response["clock_epoch"].is<double>()) {
        resultNote += " (Shelly clock " + String(response["clock_epoch"].as<double>(), 0) + ")";
    }
    if (note) *note = resultNote.c_str();
    http.end();
    return code == 200;
}

void ShellyHandler::writeBoundedStatus(JsonObject target) {
    target["bounded"]["protocol"] = 1;
    target["bounded"]["ready"] = false;
    if (WiFi.status() != WL_CONNECTED) return;
    HTTPClient http;
    http.setTimeout(5000);
    http.begin("http://" + ip_ + "/script/" + String(SHELLY_BOUNDED_SCRIPT_ID) + "/bounded");
    int code = http.GET();
    JsonDocument status;
    if (code == 200 && !deserializeJson(status, http.getString()) && status["protocol"] == 1) {
        target["bounded"].set(status.as<JsonObjectConst>());
    }
    http.end();
    target["bounded"]["device_boot_id"] = bootId_;
}
