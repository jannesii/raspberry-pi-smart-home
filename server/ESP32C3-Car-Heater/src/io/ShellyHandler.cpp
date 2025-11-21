#include "io/ShellyHandler.h"

#include <WiFi.h>
#include <HTTPClient.h>

ShellyHandler::ShellyHandler(String ipAddress)
{
    // Build base URL: "http://192.168.33.1/rpc/Switch.Set?id=0&on="
    ip_ = ipAddress;
    baseUrl_ = String("http://") + ipAddress + "/rpc/Switch.Set?id=0&on=";
    Serial.printf("[Shelly] Initialized with base URL: %s\n", baseUrl_.c_str());
}


bool ShellyHandler::switchOn()
{
    return sendSwitchRequest(true);
}

bool ShellyHandler::switchOff()
{
    return sendSwitchRequest(false);
}

bool ShellyHandler::toggle()
{
    bool isOn;
    if (!getStatus(isOn))
    {
        Serial.println("[Shelly] Failed to get current status for toggle");
        return false;
    }
    return sendSwitchRequest(!isOn);
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