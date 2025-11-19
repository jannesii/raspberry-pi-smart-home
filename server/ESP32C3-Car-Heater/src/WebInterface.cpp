#include <Arduino.h>
#include <LittleFS.h>
#include <ESPAsyncWebServer.h>
#include <ArduinoJson.h>
#include <esp_system.h>

#include "wifihelper.h"
#include "measurements.h"
#include "timekeeper.h"
#include "WebInterface.h"

WebInterface::WebInterface(AsyncWebServer &server,
                           Config &config,
                           Thermostat &thermostat,
                           ShellyHandler &shelly,
                           LogManager &logManager,
                           const String &wifiSSID,
                           LedManager &led,
                           HeaterTask &heaterTask)
    : server_(server),
      config_(config),
      thermostat_(thermostat),
      shelly_(shelly),
      logManager_(logManager),
      wifiSSID_(wifiSSID),
      led_(led),
      heaterTask_(heaterTask)
{
}

void WebInterface::begin()
{
  setupStaticRoutes();
  setupActionRoutes();
  setupApiRoutes();
}

// ----------------- setup pieces -----------------

void WebInterface::setupStaticRoutes()
{
  // Combined stylesheet referenced by all pages
  server_.serveStatic("/styles.css", LittleFS, "/styles.css");
  
  // Root: serve index.html
  server_.on("/", HTTP_GET, [this](AsyncWebServerRequest *request)
             { handleRoot(request); });
  server_.serveStatic("/index.js", LittleFS, "/index.js");



  // Logs page
  server_.on("/logs", HTTP_GET, [this](AsyncWebServerRequest *request)
             { handleLogsPage(request); });
  server_.serveStatic("/logs.js", LittleFS, "/logs.js");
}

void WebInterface::setupActionRoutes()
{
  // Sync time from browser
  server_.on("/sync-time", HTTP_POST, [this](AsyncWebServerRequest *request)
             { handleSyncTime(request); });

  // Update config from POST form
  server_.on("/set-config", HTTP_POST, [this](AsyncWebServerRequest *request)
             { handleSetConfig(request); });

  // Clear logs
  server_.on("/logs/clear", HTTP_POST, [this](AsyncWebServerRequest *request)
             { handleLogsClear(request); });
}

void WebInterface::setupApiRoutes()
{
  server_.on("/api/status", HTTP_GET, [this](AsyncWebServerRequest *request)
             { handleApiStatus(request); });

  server_.on("/api/logs", HTTP_GET, [this](AsyncWebServerRequest *request)
             { handleApiLogs(request); });

  server_.on("/api/reboot", HTTP_POST, [this](AsyncWebServerRequest *request)
             {
               Serial.println("[Web] Reboot request received");
               request->send(200, "text/plain", "Rebooting...");
               delay(100);
               esp_restart();
             });
}

// ----------------- handlers -----------------

void WebInterface::handleRoot(AsyncWebServerRequest *request)
{
  if (showDebug_)
  {
    Serial.println("[Web] Serving /index.html from FS");
  }
  auto *res = request->beginResponse(LittleFS, "/index.html.gz", "text/html");
  res->addHeader("Content-Encoding", "gzip");
  request->send(res);
}

void WebInterface::handleLogsPage(AsyncWebServerRequest *request)
{
  if (showDebug_)
  {
    Serial.println("[Web] Serving /logs.html from FS");
  }
  auto *res = request->beginResponse(LittleFS, "/logs.html.gz", "text/html");
  res->addHeader("Content-Encoding", "gzip");
  request->send(res);
}

void WebInterface::handleSyncTime(AsyncWebServerRequest *request)
{
  Serial.println("[Web] Time sync request received");
  if (!request->hasParam("epoch", true) || !request->hasParam("tz", true))
  {
    request->send(400, "text/plain", "Missing epoch or tz");
    return;
  }

  auto pEpoch = request->getParam("epoch", true);
  auto pTz = request->getParam("tz", true);

  uint64_t epoch = strtoull(pEpoch->value().c_str(), nullptr, 10);
  int16_t tzMin = static_cast<int16_t>(strtol(pTz->value().c_str(), nullptr, 10));

  timekeeper::setUtcWithOffset(epoch, tzMin);
  led_.blinkSingle();

  request->redirect("/");
}

void WebInterface::handleSetConfig(AsyncWebServerRequest *request)
{
  if (request->hasParam("target", true))
  {
    auto p = request->getParam("target", true);
    float t = p->value().toFloat();
    config_.setTargetTemp(t);
    thermostat_.setTarget(t);
  }
  if (request->hasParam("hyst", true))
  {
    auto p = request->getParam("hyst", true);
    float h = p->value().toFloat();
    config_.setHysteresis(h);
    thermostat_.setHysteresis(h);
  }
  if (request->hasParam("taskdelay", true))
  {
    auto p = request->getParam("taskdelay", true);
    float d = p->value().toFloat();
    config_.setHeaterTaskDelayS(d);
  }
  if (request->hasParam("dzstart", true))
  {
    auto p = request->getParam("dzstart", true);
    String v = p->value();
    uint16_t h = v.substring(0, 2).toInt();
    uint16_t m = v.substring(3, 5).toInt();
    config_.setDeadzoneStartMin(h * 60 + m);
  }
  if (request->hasParam("dzend", true))
  {
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

  float currentTemp = takeMeasurement().temperature;
  String currentTime = timekeeper::isValid()
                           ? timekeeper::formatLocal()
                           : "Not set";

  JsonDocument doc;
  doc["wifi_ssid"] = wifiSSID_;
  doc["temp"] = currentTemp;
  doc["is_on"] = shelly_.getStatus(isOn) ? isOn : false;
  doc["current_time"] = currentTime;
  doc["time_synced"] = timekeeper::isValid();
  doc["in_deadzone"] = heaterTask_.isInDeadzone();
  doc["dz_enabled"] = heaterTask_.isDeadzoneEnabled();
  doc["heater_task_enabled"] = heaterTask_.isEnabled();

  doc["target_temp"] = config_.targetTemp();
  doc["hyst"] = config_.hysteresis();
  doc["task_delay"] = config_.heaterTaskDelayS();
  doc["dz_start"] = fmtHHMM(config_.deadzoneStartMin());
  doc["dz_end"] = fmtHHMM(config_.deadzoneEndMin());

  String json;
  serializeJson(doc, json);
  request->send(200, "application/json", json);
}

void WebInterface::handleApiLogs(AsyncWebServerRequest *request)
{
  String logs = logManager_.toStringNewestFirst();
  if (logs.isEmpty())
  {
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
