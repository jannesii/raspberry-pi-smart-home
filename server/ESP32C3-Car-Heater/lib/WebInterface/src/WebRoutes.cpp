#include <Arduino.h>
#include <LittleFS.h>
#include <ESPAsyncWebServer.h>
#include <ArduinoJson.h>

#include "wifihelper.h"
#include "WebRoutes.h"
#include "measurements.h"
#include "timekeeper.h"
#include "Config.h"
#include "Thermostat.h"
#include "ShellyHandler.h"
#include "LogManager.h"
#include "LedManager.h"
#include "WebSocketRoutes.h"
#include "WebSocketRoutes.h"

// no longer need: #include "webtemplates.h"

static String fmtHHMM(uint16_t minutes);

void setupRoutes(AsyncWebServer &server,
                 Config &config,
                 Thermostat &thermostat,
                 ShellyHandler &shelly,
                 LogManager &logManager,
                 String wifiSSID,
                 LedManager &led)
{
  // Attach WebSocket endpoint
  setupWebSocket(server);

  logManager.setCallback([](const String &line) {
    wsBroadcastLogLine(line);
  });

  wsSetToggleCallback([&shelly, &led]() {
    Serial.println("[WS] Toggle heater command received");
    bool ok = shelly.toggle();
    if (ok) {
      led.blinkSingle();
    } else {
      led.blinkTriple();
    }
    // HeaterTask will push the updated state via wsBroadcastTempUpdate()
    // on its next cycle.
  });
  // -------------------------
  // STATIC FRONTEND FILES
  // -------------------------

  // Root: serve index.html from FS (no variable injection)
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request)
            {
    Serial.println("[Web] Serving /index.html from FS");
    request->send(LittleFS, "/index.html", "text/html"); });

  // JS / CSS / other assets
  server.serveStatic("/index.js", LittleFS, "/index.js");
  server.serveStatic("/index.css", LittleFS, "/index.css");

  // Logs page + JS
  server.on("/logs", HTTP_GET, [](AsyncWebServerRequest *request)
            {
    Serial.println("[Web] Serving /logs.html from FS");
    request->send(LittleFS, "/logs.html", "text/html"); });
  server.serveStatic("/logs.js", LittleFS, "/logs.js");
  server.serveStatic("/logs.css", LittleFS, "/logs.css");
  // -------------------------
  // API / ACTION ENDPOINTS
  // -------------------------

  // Toggle heater
  server.on("/toggle", HTTP_POST, [&shelly, &led](AsyncWebServerRequest *request)
            {
    Serial.println("[Web] Toggle heater request received");
    bool ok = shelly.toggle();
    if (ok) {
      led.blinkSingle();
    } else {
      led.blinkTriple();
    }
    // after you have SPA-style frontend, you might change this
    // to `request->send(200, "text/plain", ok ? "OK" : "ERR");`
    request->redirect("/"); });

  // Sync time from browser
  server.on("/sync-time", HTTP_POST, [&led](AsyncWebServerRequest *request)
            {
    Serial.println("[Web] Time sync request received");
    if (!request->hasParam("epoch", true) || !request->hasParam("tz", true)) {
      request->send(400, "text/plain", "Missing epoch or tz");
      return;
    }

    auto pEpoch = request->getParam("epoch", true);
    auto pTz    = request->getParam("tz", true);

    uint64_t epoch = strtoull(pEpoch->value().c_str(), nullptr, 10);
    int16_t tzMin  = static_cast<int16_t>(strtol(pTz->value().c_str(), nullptr, 10));

    timekeeper::setUtcWithOffset(epoch, tzMin);
    led.blinkSingle();

    request->redirect("/"); });

  // Update config from POST form (you can later convert this to JSON / WS if you want)
  server.on("/set-config", HTTP_POST,
            [&config, &thermostat, &led](AsyncWebServerRequest *request)
            {
              if (request->hasParam("target", true))
              {
                auto p = request->getParam("target", true);
                float t = p->value().toFloat();
                config.setTargetTemp(t);
                thermostat.setTarget(t);
              }
              if (request->hasParam("hyst", true))
              {
                auto p = request->getParam("hyst", true);
                float h = p->value().toFloat();
                config.setHysteresis(h);
                thermostat.setHysteresis(h);
              }
              if (request->hasParam("taskdelay", true))
              {
                auto p = request->getParam("taskdelay", true);
                float d = p->value().toFloat();
                config.setHeaterTaskDelayS(d);
              }
              if (request->hasParam("dzstart", true))
              {
                auto p = request->getParam("dzstart", true);
                String v = p->value();
                uint16_t h = v.substring(0, 2).toInt();
                uint16_t m = v.substring(3, 5).toInt();
                config.setDeadzoneStartMin(h * 60 + m);
              }
              if (request->hasParam("dzend", true))
              {
                auto p = request->getParam("dzend", true);
                String v = p->value();
                uint16_t h = v.substring(0, 2).toInt();
                uint16_t m = v.substring(3, 5).toInt();
                config.setDeadzoneEndMin(h * 60 + m);
              }

              config.save();
              led.blinkSingle();

              request->redirect("/");
            });

  // Clear logs (UI can POST here from logs.html/logs.js)
  server.on("/logs/clear", HTTP_POST, [&logManager, &led](AsyncWebServerRequest *request)
            {
    logManager.clear();
    led.blinkSingle();
    request->redirect("/logs"); });

  // Optional: later, add /api/status, /api/logs etc. for WebSocket/REST
  server.on("/api/status", HTTP_GET,
            [&shelly, &config, wifiSSID](AsyncWebServerRequest *request)
            {
              bool isOn;
              String heaterState;
              String heaterBtnClass;
              String heaterBtnLabel;

              if (!shelly.getStatus(isOn))
              {
                Serial.println("[Web] Failed to get Shelly status for /api/status");
                heaterState = "Unknown";
                heaterBtnClass = "unknown";
                heaterBtnLabel = "Retry";
              }
              else
              {
                heaterState = isOn ? "ON" : "OFF";
                heaterBtnClass = isOn ? "off" : "on";
                heaterBtnLabel = isOn ? "Turn OFF" : "Turn ON";
              }

              float currentTemp = takeMeasurement().temperature;
              String currentTime = timekeeper::isValid()
                                       ? timekeeper::formatLocal()
                                       : "Not set";

              StaticJsonDocument<512> doc;
              doc["wifi_ssid"] = wifiSSID;
              doc["temp"] = currentTemp;
              doc["heater_state"] = heaterState;
              doc["heater_btn_class"] = heaterBtnClass;
              doc["heater_btn_label"] = heaterBtnLabel;
              doc["current_time"] = currentTime;
              doc["time_synced"] = timekeeper::isValid();

              doc["target_temp"] = config.targetTemp();
              doc["hyst"] = config.hysteresis();
              doc["task_delay"] = config.heaterTaskDelayS();
              doc["dz_start"] = fmtHHMM(config.deadzoneStartMin()); // "HH:MM"
              doc["dz_end"] = fmtHHMM(config.deadzoneEndMin());

              String json;
              serializeJson(doc, json);
              request->send(200, "application/json", json);
            });

  server.on("/api/logs", HTTP_GET, [&logManager](AsyncWebServerRequest *request) {
    String logs = logManager.toStringNewestFirst();  // already newest → oldest
    if (logs.isEmpty()) {
      logs = "No log entries yet.";
    }
    request->send(200, "text/plain", logs);
  });
}

static String fmtHHMM(uint16_t minutes)
{
  uint16_t h = (minutes / 60) % 24;
  uint16_t m = minutes % 60;
  char buf[6];
  snprintf(buf, sizeof(buf), "%02u:%02u", h, m);
  return String(buf);
}
