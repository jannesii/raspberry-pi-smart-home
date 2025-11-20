#include "io/WebSocketHub.h"
#include <ArduinoJson.h>
#include "core/TimeKeeper.h"
#include "heating/HeatingCalculator.h"
#include "io/measurements.h"

WebSocketHub::WebSocketHub(
    AsyncWebServer &server,
    HeaterTask &heaterTask,
    ReadyByTask &readyByTask,
    Config &config,
    KFactorCalibrationManager &calibration)
    : ws_("/ws"),
      server_(server),
      heaterTask_(heaterTask),
      readyByTask_(readyByTask),
      config_(config),
      calibration_(calibration)
{
}

void WebSocketHub::begin()
{
  // Attach event handler with a lambda that forwards to the member function
  ws_.onEvent([this](AsyncWebSocket *server,
                     AsyncWebSocketClient *client,
                     AwsEventType type,
                     void *arg,
                     uint8_t *data,
                     size_t len)
              { this->onEvent(server, client, type, arg, data, len); });

  server_.addHandler(&ws_);
}

void WebSocketHub::onEvent(AsyncWebSocket *server,
                           AsyncWebSocketClient *client,
                           AwsEventType type,
                           void *arg,
                           uint8_t *data,
                           size_t len)
{
  switch (type)
  {
  case WS_EVT_CONNECT:
    Serial.printf("[WS] Client #%u connected from %s\n",
                  client->id(),
                  client->remoteIP().toString().c_str());
    broadcastTimeSync();
    broadcastTempUpdate();
    broadcastReadyByUpdate();
    broadcastCalibrationUpdate();
    break;

  case WS_EVT_DISCONNECT:
    Serial.printf("[WS] Client #%u disconnected\n", client->id());
    break;

  case WS_EVT_DATA:
  {
    AwsFrameInfo *info = (AwsFrameInfo *)arg;
    if (info->final && info->index == 0 && info->len == len && info->opcode == WS_TEXT)
    {
      String msg;
      msg.reserve(len);
      for (size_t i = 0; i < len; i++)
      {
        msg += (char)data[i];
      }
      Serial.printf("[WS] Received: %s\n", msg.c_str());

      if (calibration_.isBusy())
      {
        // Ignore control toggles while calibration is active
        break;
      }

      if (msg == "toggle_heater")
        toggleHeater();
      if (msg == "toggle_deadzone")
        toggleDeadzone();
      if (msg == "toggle_heater_task")
        toggleHeaterTask();

      broadcastTempUpdate();
    }
    break;
  }

  case WS_EVT_PONG:
    // optional: Serial.println("[WS] Pong");
    break;

  case WS_EVT_ERROR:
    Serial.printf("[WS] Error on client #%u\n", client->id());
    break;
  }
}

void WebSocketHub::broadcastTimeSync()
{
  if (!ws_.count())
    return;

  JsonDocument doc;
  doc["type"] = "time_sync";
  doc["time_synced"] = timekeeper::isTrulyValid();

  String json;
  serializeJson(doc, json);
  ws_.textAll(json);
}

void WebSocketHub::broadcastLogLine(const String &line)
{
  if (!ws_.count())
    return;

  JsonDocument doc;
  doc["type"] = "log_append";
  doc["line"] = line;

  String json;
  serializeJson(doc, json);
  ws_.textAll(json);
}

void WebSocketHub::broadcastTempUpdate()
{
  if (!ws_.count())
    return;
  String currentTime = timekeeper::isValid()
                           ? timekeeper::formatLocal()
                           : "Not set";
  JsonDocument doc;
  doc["type"] = "temp_update";
  doc["temp"] = heaterTask_.currentTemp();
  doc["is_on"] = heaterTask_.isHeaterOn();
  doc["time_synced"] = timekeeper::isTrulyValid();
  doc["current_time"] = currentTime;
  doc["in_deadzone"] = heaterTask_.isInDeadzone();
  doc["dz_enabled"] = heaterTask_.isDeadzoneEnabled();
  doc["heater_task_enabled"] = heaterTask_.isEnabled();

  String json;
  serializeJson(doc, json);
  ws_.textAll(json);
}

void WebSocketHub::broadcastReadyByUpdate()
{
  if (!ws_.count())
    return;
  JsonDocument doc;

  doc["type"] = "ready_by_update";

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
  ws_.textAll(json);
}

void WebSocketHub::broadcastCalibrationUpdate()
{
  if (!ws_.count())
    return;

  KFactorCalibrationManager::Status st = calibration_.status();
  JsonDocument doc;
  doc["type"] = "calibration_update";
  doc["state"] = (st.state == KFactorCalibrationManager::State::Idle)
                     ? "idle"
                     : (st.state == KFactorCalibrationManager::State::Scheduled ? "scheduled" : "running");
  doc["target_temp_c"] = st.targetTempC;
  doc["start_epoch_utc"] = st.startEpochUtc;
  doc["ambient_start_c"] = st.ambientStartC;
  doc["current_temp_c"] = st.currentTempC;
  doc["elapsed_seconds"] = st.elapsedSeconds;
  doc["suggested_k"] = st.suggestedK;
  doc["current_k"] = config_.kFactor();
  doc["time_synced"] = timekeeper::isTrulyValid();
  doc["auto_enabled"] = config_.autoCalibrationEnabled();
  doc["auto_start_min"] = config_.autoCalibStartMin();
  doc["auto_end_min"] = config_.autoCalibEndMin();
  doc["auto_target_cap_c"] = config_.autoCalibTargetCapC();
  doc["current_temp"] = heaterTask_.currentTemp();

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
  ws_.textAll(json);
}

void WebSocketHub::toggleDeadzone()
{
  heaterTask_.setDeadzoneEnabled(!heaterTask_.isDeadzoneEnabled());
}

void WebSocketHub::toggleHeaterTask()
{
  heaterTask_.setEnabled(!heaterTask_.isEnabled());
}

void WebSocketHub::toggleHeater()
{
  if (heaterTask_.isHeaterOn())
    heaterTask_.turnHeaterOff();
  else
    heaterTask_.turnHeaterOn();
}
