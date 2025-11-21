#include <Arduino.h>
#include <LittleFS.h>
#include <ESPAsyncWebServer.h>
#include <ArduinoJson.h>
#include <esp_system.h>

#include "io/wifihelper.h"
#include "io/measurements.h"
#include "core/TimeKeeper.h"
#include "ui/WebInterface.h"
#include "heating/ReadyByTask.h"
#include "heating/HeatingCalculator.h"
#include "heating/KFactorCalibrator.h"

WebInterface::WebInterface(AsyncWebServer &server,
                           Config &config,
                           Thermostat &thermostat,
                           ShellyHandler &shelly,
                           LogManager &logManager,
                           const String &wifiSSID,
                           LedManager &led,
                           HeaterTask &heaterTask,
                           ReadyByTask &readyByTask,
                           KFactorCalibrationManager &calibration)
    : server_(server),
      config_(config),
      thermostat_(thermostat),
      shelly_(shelly),
      logManager_(logManager),
      wifiSSID_(wifiSSID),
      led_(led),
      heaterTask_(heaterTask),
      readyByTask_(readyByTask),
      calibration_(calibration)
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

  server_.on("/calibrate", HTTP_GET, [this](AsyncWebServerRequest *request)
             { handleCalibratePage(request); });
  server_.serveStatic("/calibrate.js", LittleFS, "/calibrate.js");

  server_.on("/ready-by", HTTP_GET, [this](AsyncWebServerRequest *request)
             { handleReadyBy(request); });
  server_.serveStatic("/readyby.js", LittleFS, "/readyby.js");

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
               esp_restart(); });

  server_.on("/api/ready-by/clear", HTTP_POST, [this](AsyncWebServerRequest *request)
             {
              Serial.println("[Web] Cancel Ready By request received");
              readyByTask_.cancel();
              request->send(200, "application/json",
                            "{\"ok\":true,\"scheduled\":false}"); });
  server_.on("/api/ready-by", HTTP_GET, [this](AsyncWebServerRequest *request)
             {Serial.println("[Web] GET /api/ready-by request received");
              handleReadyByStatus(request); });
  server_.on("/api/ready-by", HTTP_POST, [this](AsyncWebServerRequest *request)
             {Serial.println("[Web] POST /api/ready-by request received"); 
              handleReadyBySchedule(request); });

  // Calibration
  server_.on("/api/calibration/status", HTTP_GET, [this](AsyncWebServerRequest *request)
             { handleCalibrationStatus(request); });
  server_.on("/api/calibration/start", HTTP_POST, [this](AsyncWebServerRequest *request)
             { handleCalibrationStart(request); });
  server_.on("/api/calibration/cancel", HTTP_POST, [this](AsyncWebServerRequest *request)
             { handleCalibrationCancel(request); });
  server_.on("/api/calibration/delete", HTTP_POST, [this](AsyncWebServerRequest *request)
             { handleCalibrationDelete(request); });
  server_.on("/api/calibration/settings", HTTP_POST, [this](AsyncWebServerRequest *request)
             { handleCalibrationSettings(request); });
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

void WebInterface::handleReadyBy(AsyncWebServerRequest *request)
{
  if (showDebug_)
  {
    Serial.println("[Web] Serving /ready-by from FS");
  }
  auto *res = request->beginResponse(LittleFS, "/readyby.html.gz", "text/html");
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

void WebInterface::handleCalibratePage(AsyncWebServerRequest *request)
{
  if (showDebug_)
  {
    Serial.println("[Web] Serving /calibrate.html from FS");
  }
  auto *res = request->beginResponse(LittleFS, "/calibrate.html.gz", "text/html");
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

  request->send(200, "text/plain", "Time synchronized");
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
  doc["time_synced"] = timekeeper::isTrulyValid();
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
  JsonDocument doc;
  String logs = logManager_.toStringNewestFirst();
  if (logs.isEmpty())
  {
    logs = "No log entries yet.";
  }
  doc["logs"] = logs;
  doc["time_synced"] = timekeeper::isTrulyValid();
  String json;
  serializeJson(doc, json);
  request->send(200, "application/json", json);
}

void WebInterface::handleReadyByStatus(AsyncWebServerRequest *request)
{
  JsonDocument doc;

  // If time invalid or no schedule → just "scheduled: false"
  if (!timekeeper::isValid())
  {
    doc["scheduled"] = false;
  }
  else
  {
    uint64_t targetEpoch = 0;
    float targetTemp = 0.0f;

    if (!readyByTask_.getSchedule(targetEpoch, targetTemp))
    {
      doc["scheduled"] = false;
    }
    else
    {
      doc["scheduled"] = true;
      doc["target_epoch_utc"] = targetEpoch;
      doc["target_temp_c"] = targetTemp;

      // Current state
      uint64_t nowUtc = timekeeper::nowUtc();
      doc["now_epoch_utc"] = nowUtc;

      float ambient = takeMeasurement(false).temperature;
      doc["ambient_temp_c"] = ambient;

      // Use same physics as ReadyBy to estimate warmup / start time
      HeatingCalculator calc;
      float k = calibration_.derivedKFor(ambient, targetTemp);
      float warmupSec = calc.estimateWarmupSeconds(k, ambient, targetTemp);
      if (warmupSec < 0.0f)
        warmupSec = 0.0f;

      doc["warmup_seconds"] = warmupSec;

      uint64_t warmup = static_cast<uint64_t>(warmupSec);
      uint64_t secondsLeft = (targetEpoch > nowUtc) ? (targetEpoch - nowUtc) : 0;

      uint64_t startEpochUtc;
      if (warmup >= secondsLeft)
      {
        startEpochUtc = nowUtc; // start ASAP
      }
      else
      {
        startEpochUtc = targetEpoch - warmup;
      }

      doc["start_epoch_utc"] = startEpochUtc;
    }
  }
  doc["current_temp"] = takeMeasurement(false).temperature;
  doc["time_synced"] = timekeeper::isTrulyValid();

  String json;
  serializeJson(doc, json);
  request->send(200, "application/json", json);
}

void WebInterface::handleReadyBySchedule(AsyncWebServerRequest *request)
{
  if (!request->hasParam("target_epoch_utc", true) ||
      !request->hasParam("target_temp_c", true))
  {
    request->send(400, "application/json",
                  "{\"ok\":false,\"error\":\"missing params\"}");
    return;
  }

  auto pEpoch = request->getParam("target_epoch_utc", true);
  auto pTemp = request->getParam("target_temp_c", true);

  uint64_t targetEpoch = strtoull(pEpoch->value().c_str(), nullptr, 10);
  float targetTemp = pTemp->value().toFloat();

  readyByTask_.schedule(targetEpoch, targetTemp);

  // Optional: compute warmup & start like GET /api/ready-by does
  JsonDocument doc;
  doc["ok"] = true;
  doc["scheduled"] = true;
  doc["target_epoch_utc"] = targetEpoch;
  doc["target_temp_c"] = targetTemp;

  if (timekeeper::isValid())
  {
    uint64_t nowUtc = timekeeper::nowUtc();
    doc["now_epoch_utc"] = nowUtc;

    float ambient = takeMeasurement(false).temperature;
    HeatingCalculator calc;
    float warmupSec = calc.estimateWarmupSeconds(config_.kFactor(), ambient, targetTemp);
    if (warmupSec < 0.0f)
      warmupSec = 0.0f;

    doc["warmup_seconds"] = warmupSec;

    uint64_t warmup = static_cast<uint64_t>(warmupSec);
    uint64_t secondsLeft = (targetEpoch > nowUtc) ? (targetEpoch - nowUtc) : 0;

    uint64_t startEpochUtc;
    if (warmup >= secondsLeft)
    {
      startEpochUtc = nowUtc;
    }
    else
    {
      startEpochUtc = targetEpoch - warmup;
    }
    doc["start_epoch_utc"] = startEpochUtc;
  }

  String json;
  serializeJson(doc, json);
  request->send(200, "application/json", json);
}

void WebInterface::handleCalibrationStatus(AsyncWebServerRequest *request)
{
  auto st = calibration_.status();
  JsonDocument doc;
  doc["ok"] = true;
  doc["state"] = (st.state == KFactorCalibrationManager::State::Idle)
                     ? "idle"
                     : (st.state == KFactorCalibrationManager::State::Scheduled ? "scheduled" : "running");
  doc["target_temp_c"] = st.targetTempC;
  doc["start_epoch_utc"] = st.startEpochUtc;
  doc["ambient_start_c"] = st.ambientStartC;
  doc["current_temp_c"] = st.currentTempC;
  doc["elapsed_seconds"] = st.elapsedSeconds;
  doc["suggested_k"] = st.suggestedK;
  doc["time_synced"] = timekeeper::isTrulyValid();
  doc["current_k"] = config_.kFactor();
  doc["auto_enabled"] = config_.autoCalibrationEnabled();
  doc["auto_start_min"] = config_.autoCalibStartMin();
  doc["auto_end_min"] = config_.autoCalibEndMin();
  doc["auto_target_cap_c"] = config_.autoCalibTargetCapC();
  doc["current_temp"] = takeMeasurement(false).temperature;

  JsonArray recs = doc["records"].to<JsonArray>();
  for (size_t i = 0; i < st.recordCount && i < st.records.size(); ++i)
  {
    const auto &r = st.records[i];
    JsonObject o = recs.add<JsonObject>();
    o["ambient_c"] = r.ambientC;
    o["target_c"] = r.targetC;
    o["warmup_seconds"] = r.warmupSeconds;
    o["k"] = r.kFactor;
    o["epoch_utc"] = r.epochUtc;
  }

  String json;
  serializeJson(doc, json);
  request->send(200, "application/json", json);
}

void WebInterface::handleCalibrationStart(AsyncWebServerRequest *request)
{
  const bool fromBody = true;
  if (!request->hasParam("target", fromBody))
  {
    request->send(400, "application/json", "{\"ok\":false,\"error\":\"missing target\"}");
    return;
  }

  float target = request->getParam("target", fromBody)->value().toFloat();
  uint64_t startEpoch = 0;
  if (request->hasParam("start_epoch_utc", fromBody))
  {
    startEpoch = strtoull(request->getParam("start_epoch_utc", fromBody)->value().c_str(), nullptr, 10);
  }

  String err;
  if (!calibration_.schedule(target, startEpoch, err))
  {
    String resp = "{\"ok\":false,\"error\":\"" + err + "\"}";
    request->send(400, "application/json", resp);
    return;
  }

  JsonDocument doc;
  doc["ok"] = true;
  doc["state"] = (startEpoch == 0) ? "running" : "scheduled";

  String json;
  serializeJson(doc, json);
  request->send(200, "application/json", json);
}

void WebInterface::handleCalibrationCancel(AsyncWebServerRequest *request)
{
  bool cancelled = calibration_.cancel();
  JsonDocument doc;
  doc["ok"] = cancelled;
  String json;
  serializeJson(doc, json);
  request->send(200, "application/json", json);
}

void WebInterface::handleCalibrationDelete(AsyncWebServerRequest *request)
{
  const bool fromBody = true;
  if (!request->hasParam("epoch_utc", fromBody))
  {
    request->send(400, "application/json",
                  "{\"ok\":false,\"error\":\"missing epoch_utc\"}");
    return;
  }

  uint64_t epoch = strtoull(request->getParam("epoch_utc", fromBody)->value().c_str(), nullptr, 10);
  bool deleted = false;
  if (epoch > 0)
  {
    deleted = calibration_.deleteRecord(epoch);
  }

  JsonDocument doc;
  doc["ok"] = deleted;
  if (!deleted)
  {
    doc["error"] = "not found";
  }
  String json;
  serializeJson(doc, json);
  request->send(deleted ? 200 : 404, "application/json", json);
}

void WebInterface::handleCalibrationSettings(AsyncWebServerRequest *request)
{
  const bool fromBody = true;
  if (request->hasParam("auto_enabled", fromBody))
  {
    bool en = request->getParam("auto_enabled", fromBody)->value() == "1";
    config_.setAutoCalibrationEnabled(en);
  }
  if (request->hasParam("auto_start_min", fromBody))
  {
    config_.setAutoCalibStartMin(request->getParam("auto_start_min", fromBody)->value().toInt());
  }
  if (request->hasParam("auto_end_min", fromBody))
  {
    config_.setAutoCalibEndMin(request->getParam("auto_end_min", fromBody)->value().toInt());
  }
  if (request->hasParam("auto_target_cap_c", fromBody))
  {
    config_.setAutoCalibTargetCapC(request->getParam("auto_target_cap_c", fromBody)->value().toFloat());
  }
  config_.save();

  JsonDocument doc;
  doc["ok"] = true;
  doc["auto_enabled"] = config_.autoCalibrationEnabled();
  doc["auto_start_min"] = config_.autoCalibStartMin();
  doc["auto_end_min"] = config_.autoCalibEndMin();
  doc["auto_target_cap_c"] = config_.autoCalibTargetCapC();
  String json;
  serializeJson(doc, json);
  request->send(200, "application/json", json);
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
