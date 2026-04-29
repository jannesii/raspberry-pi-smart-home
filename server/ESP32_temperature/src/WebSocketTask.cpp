#include "WebSocketTask.h"

#include <ArduinoJson.h>
#include <WiFi.h>
#include <ctype.h>
#include <esp_system.h>
#include <string.h>

#include "AppConfig.h"
#include "Metrics.h"
#include "SensorTask.h"
#include "StructuredLog.h"
#include "TaskWatchdog.h"
#include "WifiManager.h"

WebSocketTask *WebSocketTask::instance_ = nullptr;

namespace
{
constexpr const char *kFirmwareVersion = "temperature-ws-v1";
constexpr size_t kLogSnapshotSize = 64;
StructuredLog::Entry gLogSnapshot[kLogSnapshotSize];

const char *taskStateName(eTaskState state)
{
  switch (state)
  {
  case eRunning:
    return "running";
  case eReady:
    return "ready";
  case eBlocked:
    return "blocked";
  case eSuspended:
    return "suspended";
  case eDeleted:
    return "deleted";
  default:
    return "unknown";
  }
}
}

WebSocketTask::WebSocketTask()
{
  instance_ = this;
  pendingMutex_ = xSemaphoreCreateMutex();
}

void WebSocketTask::start(uint32_t stackSize, UBaseType_t priority)
{
  if (handle_ != nullptr)
  {
    LOG_WARN(F("WebSocketTask already running"));
    return;
  }
  running_ = true;
  xTaskCreate(&WebSocketTask::taskEntry, "WebSocketTask", stackSize, this, priority, &handle_);
  LOG_INFO(F("WebSocketTask started"));
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
  }
}

void WebSocketTask::taskEntry(void *pvParameters)
{
  auto *self = static_cast<WebSocketTask *>(pvParameters);
  self->run();
}

void WebSocketTask::run()
{
  while (running_ && WiFi.status() != WL_CONNECTED)
  {
    LOG_DEBUG(F("WebSocket waiting for WiFi"));
    vTaskDelay(pdMS_TO_TICKS(1000));
  }

  TaskWatchdog::registerTask(TaskWatchdog::TaskId::WebSocket, "WebSocketTask", nullptr, 60000);
  connect();

  while (running_)
  {
    webSocket_.loop();
    TaskWatchdog::heartbeat(TaskWatchdog::TaskId::WebSocket);

    const uint32_t now = millis();
    if (connected_ && authenticated_ && (now - lastHeartbeatMs_ >= HEARTBEAT_INTERVAL_MS))
    {
      sendHeartbeat();
      lastHeartbeatMs_ = now;
    }

    if (connected_ && authenticated_)
    {
      sendPendingTelemetry();
    }

    vTaskDelay(pdMS_TO_TICKS(10));
  }

  TaskWatchdog::unregisterTask(TaskWatchdog::TaskId::WebSocket);
  disconnect();
}

void WebSocketTask::webSocketEventStatic(WStype_t type, uint8_t *payload, size_t length)
{
  WebSocketTask *self = instance_;
  if (self == nullptr)
    return;

  switch (type)
  {
  case WStype_DISCONNECTED:
    self->connected_ = false;
    self->authenticated_ = false;
    self->stats_.disconnectCount++;
    if (self->running_ && WiFi.status() == WL_CONNECTED)
    {
      const uint32_t nextDelayMs = self->reconnectDelayMs_;
      self->webSocket_.setReconnectInterval(nextDelayMs);
      self->reconnectDelayMs_ = min(self->reconnectDelayMs_ * 2, RECONNECT_DELAY_MAX_MS);
      LOGF_WARN("WebSocket disconnected; next reconnect in %lu ms",
                static_cast<unsigned long>(nextDelayMs));
    }
    break;

  case WStype_CONNECTED:
    self->connected_ = true;
    self->stats_.connectCount++;
    self->stats_.lastConnectedMs = millis();
    self->reconnectDelayMs_ = RECONNECT_DELAY_MIN_MS;
    self->webSocket_.setReconnectInterval(self->reconnectDelayMs_);
    LOG_INFO(F("WebSocket connected"));
    self->sendAuthentication();
    break;

  case WStype_TEXT:
    self->stats_.messagesReceived++;
    self->processMessage(reinterpret_cast<const char *>(payload), length);
    break;

  case WStype_PING:
    LOG_DEBUG(F("WebSocket ping received"));
    break;

  case WStype_PONG:
    LOG_DEBUG(F("WebSocket pong received"));
    break;

  case WStype_ERROR:
    LOGF_WARN("WebSocket error: %s", payload ? reinterpret_cast<const char *>(payload) : "unknown");
    break;

  default:
    break;
  }
}

void WebSocketTask::connect()
{
  AppConfig &cfg = AppConfig::get();
  host_ = cfg.getWsHost();
  path_ = cfg.getWsPath();
  port_ = cfg.getWsPort();
  useTls_ = cfg.getWsUseTls();

  if (host_.isEmpty())
  {
    LOG_WARN(F("WebSocket host not configured"));
    return;
  }
  if (path_.isEmpty())
  {
    path_ = F("/ws");
  }

  LOGF_INFO("Connecting WebSocket to %s://%s:%u%s",
            useTls_ ? "wss" : "ws",
            host_.c_str(),
            static_cast<unsigned>(port_),
            path_.c_str());

  if (useTls_)
  {
    webSocket_.beginSSL(host_.c_str(), port_, path_.c_str());
  }
  else
  {
    webSocket_.begin(host_.c_str(), port_, path_.c_str());
  }
  webSocket_.onEvent(webSocketEventStatic);
  webSocket_.setReconnectInterval(reconnectDelayMs_);
  webSocket_.enableHeartbeat(15000, 3000, 2);
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
  doc["auth"] = AppConfig::get().getWsApiKey();
  doc["device_id"] = buildDeviceId();
  doc["device_type"] = "temperature";
  doc["location"] = AppConfig::get().getDeviceLocation();
  doc["firmware_version"] = kFirmwareVersion;

  String json;
  serializeJson(doc, json);
  if (webSocket_.sendTXT(json))
  {
    stats_.messagesSent++;
  }
}

void WebSocketTask::sendHeartbeat()
{
  JsonDocument doc;
  doc["type"] = "heartbeat";
  doc["device_type"] = "temperature";
  doc["location"] = AppConfig::get().getDeviceLocation();
  doc["timestamp_ms"] = millis();

  String json;
  serializeJson(doc, json);
  if (webSocket_.sendTXT(json))
  {
    stats_.messagesSent++;
  }
}

void WebSocketTask::queueReading(float temperatureC, float humidityPct)
{
  if (xSemaphoreTake(pendingMutex_, pdMS_TO_TICKS(50)) != pdTRUE)
    return;
  pendingKind_ = PendingKind::Reading;
  pendingTemperatureC_ = temperatureC;
  pendingHumidityPct_ = humidityPct;
  pendingError_.clear();
  xSemaphoreGive(pendingMutex_);
}

void WebSocketTask::queueError(const String &message)
{
  if (xSemaphoreTake(pendingMutex_, pdMS_TO_TICKS(50)) != pdTRUE)
    return;
  pendingKind_ = PendingKind::Error;
  pendingTemperatureC_ = NAN;
  pendingHumidityPct_ = NAN;
  pendingError_ = message;
  xSemaphoreGive(pendingMutex_);
}

void WebSocketTask::queueStatusUpdate()
{
  if (xSemaphoreTake(pendingMutex_, pdMS_TO_TICKS(50)) != pdTRUE)
    return;
  if (pendingKind_ == PendingKind::None)
    pendingKind_ = PendingKind::Status;
  xSemaphoreGive(pendingMutex_);
}

void WebSocketTask::sendPendingTelemetry()
{
  PendingKind kind = PendingKind::None;
  float temperatureC = NAN;
  float humidityPct = NAN;
  String error;

  if (xSemaphoreTake(pendingMutex_, pdMS_TO_TICKS(5)) != pdTRUE)
    return;
  kind = pendingKind_;
  temperatureC = pendingTemperatureC_;
  humidityPct = pendingHumidityPct_;
  error = pendingError_;
  pendingKind_ = PendingKind::None;
  xSemaphoreGive(pendingMutex_);

  if (kind == PendingKind::None)
    return;

  JsonDocument doc;
  doc["type"] = kind == PendingKind::Error ? "temperature_error" : "temperature_reading";
  doc["device_type"] = "temperature";
  doc["device_id"] = buildDeviceId();
  doc["location"] = AppConfig::get().getDeviceLocation();
  doc["timestamp_ms"] = millis();
  if (kind == PendingKind::Reading)
  {
    doc["temperature_c"] = temperatureC;
    doc["humidity_pct"] = humidityPct;
  }
  else if (kind == PendingKind::Error)
  {
    doc["error"] = error;
  }
  appendMetrics(doc["metrics"].to<JsonObject>());
  JsonObject statsObj = doc["ws_stats"].to<JsonObject>();
  statsObj["connected"] = connected_;
  statsObj["authenticated"] = authenticated_;
  statsObj["uptime_ms"] = stats_.lastConnectedMs > 0 ? millis() - stats_.lastConnectedMs : 0;
  statsObj["messages_sent"] = stats_.messagesSent;
  statsObj["messages_received"] = stats_.messagesReceived;
  statsObj["rpc_requests_handled"] = stats_.rpcRequestsHandled;

  String json;
  serializeJson(doc, json);
  bool sent = webSocket_.sendTXT(json);
  if (sent)
  {
    stats_.messagesSent++;
  }
  if (kind == PendingKind::Reading)
  {
    Metrics::recordPostResult(Metrics::PostKind::Reading, sent);
  }
  else if (kind == PendingKind::Error)
  {
    Metrics::recordPostResult(Metrics::PostKind::Error, sent);
  }
}

void WebSocketTask::processMessage(const char *payload, size_t length)
{
  if (payload == nullptr || length == 0)
    return;

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err)
  {
    LOGF_WARN("WebSocket JSON parse error: %s", err.c_str());
    return;
  }

  if (doc["status"].is<const char *>())
  {
    const char *status = doc["status"];
    if (strcmp(status, "authenticated") == 0)
    {
      authenticated_ = true;
      queueStatusUpdate();
      LOG_INFO(F("WebSocket authenticated"));
      return;
    }
  }

  if (doc["error"].is<const char *>())
  {
    const char *error = doc["error"];
    LOGF_WARN("WebSocket server error: %s", error);
    if (strcmp(error, "unauthorized") == 0)
      reconnectDelayMs_ = RECONNECT_DELAY_MAX_MS;
    return;
  }

  if (doc["type"].is<const char *>() && strcmp(doc["type"], "rpc_request") == 0)
  {
    handleRpcRequest(doc.as<JsonVariantConst>());
    return;
  }

  if (doc.is<JsonArray>())
  {
    for (JsonObjectConst command : doc.as<JsonArrayConst>())
    {
      const char *requestId = command["request_id"] | "";
      const char *action = command["action"] | "";
      JsonDocument wrapper;
      wrapper["type"] = "rpc_request";
      wrapper["request_id"] = requestId;
      wrapper["action"] = action;
      wrapper["params"].set(command);
      handleRpcRequest(wrapper.as<JsonVariantConst>());
    }
  }
}

void WebSocketTask::handleRpcRequest(JsonVariantConst request)
{
  const char *requestId = request["request_id"] | "";
  const char *action = request["action"] | "";
  JsonVariantConst params = request["params"];
  stats_.rpcRequestsHandled++;

  JsonDocument data;

  if (strcmp(action, "get_status") == 0)
  {
    appendStatus(data.to<JsonObject>());
    sendRpcResponse(requestId, true, data.as<JsonVariantConst>());
  }
  else if (strcmp(action, "read_now") == 0)
  {
    float t = NAN, h = NAN;
    String err;
    bool ok = sensorTakeReading(t, h, err);
    if (ok)
    {
      data["location"] = AppConfig::get().getDeviceLocation();
      data["temperature_c"] = t;
      data["humidity_pct"] = h;
      queueReading(t, h);
      sendRpcResponse(requestId, true, data.as<JsonVariantConst>());
    }
    else
    {
      queueError(err);
      sendRpcError(requestId, err.c_str());
    }
  }
  else if (strcmp(action, "get_config") == 0)
  {
    AppConfig::get().toJson(data);
    sendRpcResponse(requestId, true, data.as<JsonVariantConst>());
  }
  else if (strcmp(action, "update_config") == 0)
  {
    AppConfig::get().updateFromJson(params);
    if (!params["wifi_ssid"].isNull() || !params["wifi_password"].isNull() ||
        !params["wifi_hostname"].isNull() || !params["mdns_hostname"].isNull() ||
        !params["wifi_static_ip_enabled"].isNull())
    {
      wifiManagerRequestReconnect(true);
    }
    AppConfig::get().toJson(data);
    sendRpcResponse(requestId, true, data.as<JsonVariantConst>());
  }
  else if (strcmp(action, "save_config") == 0)
  {
    bool ok = AppConfig::get().saveToNvs();
    data["persisted"] = AppConfig::get().hasPersistedConfig();
    sendRpcResponse(requestId, ok, data.as<JsonVariantConst>(), ok ? nullptr : "save_failed");
  }
  else if (strcmp(action, "discard_config") == 0)
  {
    AppConfig::get().loadDefaultsFromMacros();
    bool fromNvs = AppConfig::get().loadFromNvs();
    wifiManagerRequestReconnect(true);
    data["source"] = fromNvs ? "nvs" : "defaults";
    JsonObject cfg = data["config"].to<JsonObject>();
    AppConfig::get().toJson(cfg);
    sendRpcResponse(requestId, true, data.as<JsonVariantConst>());
  }
  else if (strcmp(action, "factory_reset") == 0)
  {
    bool ok = AppConfig::get().factoryReset();
    wifiManagerRequestReconnect(true);
    data["persisted"] = AppConfig::get().hasPersistedConfig();
    data["reboot_recommended"] = true;
    sendRpcResponse(requestId, ok, data.as<JsonVariantConst>(), ok ? nullptr : "factory_reset_failed");
  }
  else if (strcmp(action, "task_control") == 0)
  {
    const char *name = params["name"] | "";
    const char *taskAction = params["task_action"] | "";
    if (strlen(taskAction) == 0)
      taskAction = params["action"] | "";
    bool ok = false;
    if (strcmp(name, "SensorPostTask") == 0)
    {
      TaskHandle_t h = sensorTaskHandle();
      if (strcmp(taskAction, "suspend") == 0 && h)
      {
        vTaskSuspend(h);
        ok = true;
      }
      else if (strcmp(taskAction, "resume") == 0 && h)
      {
        vTaskResume(h);
        ok = true;
      }
      else if (strcmp(taskAction, "restart") == 0)
      {
        restartSensorTask();
        ok = true;
      }
    }
    data["name"] = name;
    data["action"] = taskAction;
    sendRpcResponse(requestId, ok, data.as<JsonVariantConst>(), ok ? nullptr : "unsupported_task_action");
  }
  else if (strcmp(action, "get_metrics") == 0)
  {
    appendMetrics(data.to<JsonObject>());
    sendRpcResponse(requestId, true, data.as<JsonVariantConst>());
  }
  else if (strcmp(action, "get_logs") == 0)
  {
    appendLogs(data.to<JsonObject>());
    sendRpcResponse(requestId, true, data.as<JsonVariantConst>());
  }
  else if (strcmp(action, "clear_logs") == 0)
  {
    StructuredLog::clear();
    data["cleared"] = true;
    sendRpcResponse(requestId, true, data.as<JsonVariantConst>());
  }
  else if (strcmp(action, "restart_esp") == 0)
  {
    data["restarting"] = true;
    sendRpcResponse(requestId, true, data.as<JsonVariantConst>());
    vTaskDelay(pdMS_TO_TICKS(500));
    ESP.restart();
  }
  else
  {
    sendRpcError(requestId, "unknown_action");
  }
}

void WebSocketTask::sendRpcResponse(const char *requestId, bool ok, JsonVariantConst data, const char *error)
{
  JsonDocument doc;
  doc["type"] = "rpc_response";
  doc["request_id"] = requestId ? requestId : "";
  doc["device_type"] = "temperature";
  doc["device_id"] = buildDeviceId();
  doc["ok"] = ok;
  if (ok)
  {
    doc["data"].set(data);
  }
  else
  {
    doc["error"] = error ? error : "failed";
    if (!data.isNull())
      doc["data"].set(data);
  }

  String json;
  serializeJson(doc, json);
  if (webSocket_.sendTXT(json))
  {
    stats_.messagesSent++;
  }
}

void WebSocketTask::sendRpcError(const char *requestId, const char *error)
{
  JsonDocument data;
  sendRpcResponse(requestId, false, data.as<JsonVariantConst>(), error);
}

void WebSocketTask::appendStatus(JsonObject target)
{
  target["wifi_connected"] = (WiFi.status() == WL_CONNECTED);
  target["ip"] = WiFi.localIP().toString();
  target["heap_free"] = ESP.getFreeHeap();
  target["heap_min"] = ESP.getMinFreeHeap();
  target["uptime_ms"] = millis();
  target["device_id"] = buildDeviceId();
  target["location"] = AppConfig::get().getDeviceLocation();
  JsonArray tasks = target["tasks"].to<JsonArray>();
  appendTask(tasks, "SensorPostTask", sensorTaskHandle());
  appendTask(tasks, "WebSocketTask", handle_);
}

void WebSocketTask::appendMetrics(JsonObject target)
{
  MetricsSnapshot snap = Metrics::snapshot();
  target["sensor_read_total"] = snap.sensorReadTotal;
  target["sensor_read_success"] = snap.sensorReadSuccess;
  target["sensor_read_failed"] = snap.sensorReadFailed;
  target["sensor_read_consecutive_failures"] = snap.sensorReadConsecutiveFailures;
  target["last_sensor_read_millis"] = snap.lastSensorReadMillis;
  target["last_sensor_read_success_millis"] = snap.lastSensorReadSuccessMillis;
  target["last_temperature_c"] = snap.lastTemperatureC;
  target["last_humidity_pct"] = snap.lastHumidityPct;
  target["post_reading_total"] = snap.postReadingTotal;
  target["post_reading_failed"] = snap.postReadingFailed;
  target["post_reading_consecutive_failures"] = snap.postReadingConsecutiveFailures;
  target["last_post_reading_millis"] = snap.lastPostReadingMillis;
  target["last_post_reading_success_millis"] = snap.lastPostReadingSuccessMillis;
  target["post_error_total"] = snap.postErrorTotal;
  target["post_error_failed"] = snap.postErrorFailed;
  target["post_error_consecutive_failures"] = snap.postErrorConsecutiveFailures;
  target["last_post_error_millis"] = snap.lastPostErrorMillis;
  target["last_post_error_success_millis"] = snap.lastPostErrorSuccessMillis;
  target["uptime_millis"] = snap.uptimeMillis;
  target["heap_free_bytes"] = snap.heapFreeBytes;
  target["heap_min_bytes"] = snap.heapMinBytes;
  target["wifi_connected"] = snap.wifiConnected;
  target["wifi_rssi_dbm"] = snap.wifiRssiDbm;
  target["wifi_connect_attempts_total"] = snap.wifiConnectAttempts;
  target["wifi_reconnect_events_total"] = snap.wifiReconnectEvents;
  target["wifi_last_attempt_millis"] = snap.wifiLastAttemptMillis;
  target["wifi_last_connect_millis"] = snap.wifiLastConnectedMillis;
  target["wifi_last_disconnect_millis"] = snap.wifiLastDisconnectedMillis;
  target["wifi_current_backoff_millis"] = snap.wifiCurrentBackoffMillis;
  target["wifi_connection_duration_millis"] = snap.wifiConnectionDurationMillis;
  target["wifi_current_attempt_number"] = snap.wifiCurrentAttemptNumber;
}

void WebSocketTask::appendLogs(JsonObject target)
{
  target["current_level"] = StructuredLog::levelName(StructuredLog::getLevel());
  JsonArray entries = target["entries"].to<JsonArray>();
  size_t count = StructuredLog::snapshot(gLogSnapshot, kLogSnapshotSize);
  for (size_t i = 0; i < count; ++i)
  {
    JsonObject item = entries.add<JsonObject>();
    item["timestamp_ms"] = gLogSnapshot[i].timestampMs;
    item["level"] = StructuredLog::levelName(gLogSnapshot[i].level);
    item["message"] = gLogSnapshot[i].message;
  }
}

void WebSocketTask::appendTask(JsonArray tasks, const char *name, TaskHandle_t handle)
{
  if (!handle)
    return;
  JsonObject task = tasks.add<JsonObject>();
  task["name"] = name;
  task["state"] = taskStateName(eTaskGetState(handle));
  task["stack_hwm_words"] = uxTaskGetStackHighWaterMark(handle);
  task["priority"] = uxTaskPriorityGet(handle);
}

String WebSocketTask::buildDeviceId()
{
  String source = AppConfig::get().getMdnsHostname();
  if (source.isEmpty())
    source = AppConfig::get().getDeviceLocation();
  if (source.isEmpty())
    source = F("temperature_esp32");

  String out;
  out.reserve(source.length() + 8);
  for (size_t i = 0; i < source.length(); ++i)
  {
    char c = source.charAt(i);
    if (isalnum(static_cast<unsigned char>(c)))
      out += static_cast<char>(tolower(static_cast<unsigned char>(c)));
    else if (c == '-' || c == '_')
      out += c;
    else
      out += '_';
  }
  if (!out.startsWith("temperature_"))
    out = String(F("temperature_")) + out;
  return out;
}

WebSocketTask::Stats WebSocketTask::getStats() const
{
  Stats s = stats_;
  if (connected_ && stats_.lastConnectedMs > 0)
    s.uptimeMs = millis() - stats_.lastConnectedMs;
  return s;
}
