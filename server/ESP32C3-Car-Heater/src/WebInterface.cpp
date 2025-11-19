#include <Arduino.h>
#include <LittleFS.h>
#include <ESPAsyncWebServer.h>
#include <ArduinoJson.h>

#include "wifihelper.h"
#include "measurements.h"
#include "timekeeper.h"
#include "WebSocketRoutes.h"    // your existing WS helpers
#include "WebInterface.h"

WebInterface::WebInterface(AsyncWebServer &server,
                           Config        &config,
                           Thermostat    &thermostat,
                           ShellyHandler &shelly,
                           LogManager    &logManager,
                           const String  &wifiSSID,
                           LedManager    &led)
  : server_(server),
    config_(config),
    thermostat_(thermostat),
    shelly_(shelly),
    logManager_(logManager),
    wifiSSID_(wifiSSID),
    led_(led)
{}

void WebInterface::begin()
{
  setupWebSocketIntegration();
  setupStaticRoutes();
  setupActionRoutes();
  setupApiRoutes();
}

// ----------------- setup pieces -----------------

void WebInterface::setupWebSocketIntegration()
{
  // Attach WebSocket endpoint
  setupWebSocket(server_);

  // When a new log line is added, push it via WS
  logManager_.setCallback([](const String &line) {
    wsBroadcastLogLine(line);
  });

  // WS command "toggle_heater" → this callback
  wsSetToggleCallback([this]() {
    Serial.println("[WS] Toggle heater command received");
    handleToggle();
    // HeaterTask will still push periodic updates;
    // you can optionally push an immediate one here too.
  });
}

void WebInterface::setupStaticRoutes()
{
  // Root: serve index.html
  server_.on("/", HTTP_GET, [this](AsyncWebServerRequest *request) {
    handleRoot(request);
  });

  // Static assets for main page
  server_.serveStatic("/index.js",  LittleFS, "/index.js");
  server_.serveStatic("/index.css", LittleFS, "/index.css");

  // Logs page
  server_.on("/logs", HTTP_GET, [this](AsyncWebServerRequest *request) {
    handleLogsPage(request);
  });
  server_.serveStatic("/logs.js",  LittleFS, "/logs.js");
  server_.serveStatic("/logs.css", LittleFS, "/logs.css");
}

void WebInterface::setupActionRoutes()
{
  // HTTP toggle (fallback when WS not used)
  server_.on("/toggle", HTTP_POST, [this](AsyncWebServerRequest *request) {
    Serial.println("[Web] Toggle heater request received (HTTP)");
    handleToggle();
    request->redirect("/");
  });

  // Sync time from browser
  server_.on("/sync-time", HTTP_POST, [this](AsyncWebServerRequest *request) {
    handleSyncTime(request);
  });

  // Update config from POST form
  server_.on("/set-config", HTTP_POST, [this](AsyncWebServerRequest *request) {
    handleSetConfig(request);
  });

  // Clear logs
  server_.on("/logs/clear", HTTP_POST, [this](AsyncWebServerRequest *request) {
    handleLogsClear(request);
  });
}

void WebInterface::setupApiRoutes()
{
  server_.on("/api/status", HTTP_GET, [this](AsyncWebServerRequest *request) {
    handleApiStatus(request);
  });

  server_.on("/api/logs", HTTP_GET, [this](AsyncWebServerRequest *request) {
    handleApiLogs(request);
  });
}

// ----------------- handlers -----------------

void WebInterface::handleRoot(AsyncWebServerRequest *request)
{
  if (showDebug_) {
    Serial.println("[Web] Serving /index.html from FS");
  }
  request->send(LittleFS, "/index.html", "text/html");
}

void WebInterface::handleLogsPage(AsyncWebServerRequest *request)
{
  if (showDebug_) {
    Serial.println("[Web] Serving /logs.html from FS");
  }
  request->send(LittleFS, "/logs.html", "text/html");
}

void WebInterface::handleToggle()
{
  bool ok = shelly_.toggle();
  if (ok) {
    led_.blinkSingle();
  } else {
    led_.blinkTriple();
  }

  // Optional: immediate WS temp/state push here
  // bool isOn = false;
  // shelly_.getStatus(isOn, false);
  // float currentTemp = takeMeasurement(false).temperature;
  // String nowStr = timekeeper::isValid() ? timekeeper::formatLocal() : "Not set";
  // bool inDeadzone = wsGetLastInDeadzone();
  // wsBroadcastTempUpdate(currentTemp, isOn, inDeadzone, nowStr);
}

void WebInterface::handleSyncTime(AsyncWebServerRequest *request)
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
  led_.blinkSingle();

  request->redirect("/");
}

void WebInterface::handleSetConfig(AsyncWebServerRequest *request)
{
  if (request->hasParam("target", true)) {
    auto p = request->getParam("target", true);
    float t = p->value().toFloat();
    config_.setTargetTemp(t);
    thermostat_.setTarget(t);
  }
  if (request->hasParam("hyst", true)) {
    auto p = request->getParam("hyst", true);
    float h = p->value().toFloat();
    config_.setHysteresis(h);
    thermostat_.setHysteresis(h);
  }
  if (request->hasParam("taskdelay", true)) {
    auto p = request->getParam("taskdelay", true);
    float d = p->value().toFloat();
    config_.setHeaterTaskDelayS(d);
  }
  if (request->hasParam("dzstart", true)) {
    auto p = request->getParam("dzstart", true);
    String v = p->value();
    uint16_t h = v.substring(0, 2).toInt();
    uint16_t m = v.substring(3, 5).toInt();
    config_.setDeadzoneStartMin(h * 60 + m);
  }
  if (request->hasParam("dzend", true)) {
    auto p = request->getParam("dzend", true);
    String v = p->value();
    uint16_t h = v.substring(0, 2).toInt();
    uint16_t m = v.substring(3, 5).toInt();
    config_.setDeadzoneEndMin(h * 60 + m);
  }

  config_.save();
  led_.blinkSingle();
  request->redirect("/");
}

void WebInterface::handleLogsClear(AsyncWebServerRequest *request)
{
  logManager_.clear();
  led_.blinkSingle();
  request->redirect("/logs");
}

void WebInterface::handleApiStatus(AsyncWebServerRequest *request)
{
  bool isOn;
  String heaterState;
  String heaterBtnClass;
  String heaterBtnLabel;

  if (!shelly_.getStatus(isOn)) {
    Serial.println("[Web] Failed to get Shelly status for /api/status");
    heaterState    = "Unknown";
    heaterBtnClass = "unknown";
    heaterBtnLabel = "Retry";
  } else {
    heaterState    = isOn ? "ON" : "OFF";
    heaterBtnClass = isOn ? "off" : "on";
    heaterBtnLabel = isOn ? "Turn OFF" : "Turn ON";
  }

  float currentTemp  = takeMeasurement().temperature;
  String currentTime = timekeeper::isValid()
                         ? timekeeper::formatLocal()
                         : "Not set";

  StaticJsonDocument<512> doc;
  doc["wifi_ssid"]        = wifiSSID_;
  doc["temp"]             = currentTemp;
  doc["heater_state"]     = heaterState;
  doc["heater_btn_class"] = heaterBtnClass;
  doc["heater_btn_label"] = heaterBtnLabel;
  doc["current_time"]     = currentTime;
  doc["time_synced"]      = timekeeper::isValid();

  doc["target_temp"] = config_.targetTemp();
  doc["hyst"]        = config_.hysteresis();
  doc["task_delay"]  = config_.heaterTaskDelayS();
  doc["dz_start"]    = fmtHHMM(config_.deadzoneStartMin());
  doc["dz_end"]      = fmtHHMM(config_.deadzoneEndMin());

  String json;
  serializeJson(doc, json);
  request->send(200, "application/json", json);
}

void WebInterface::handleApiLogs(AsyncWebServerRequest *request)
{
  String logs = logManager_.toStringNewestFirst();
  if (logs.isEmpty()) {
    logs = "No log entries yet.";
  }
  request->send(200, "text/plain", logs);
}

// ----------------- helper -----------------

String WebInterface::fmtHHMM(uint16_t minutes) const
{
  uint16_t h = (minutes / 60) % 24;
  uint16_t m = minutes % 60;
  char buf[6];
  snprintf(buf, sizeof(buf), "%02u:%02u", h, m);
  return String(buf);
}
