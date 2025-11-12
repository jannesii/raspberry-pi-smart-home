#include "HttpServerTask.h"

#include <WebServer.h>
#include <WiFi.h>

#include <ArduinoJson.h>

#include "AppConfig.h"
#include "SensorTask.h"
#include "Metrics.h"
#include "WifiManager.h"
#include "StructuredLog.h"
#include "TaskWatchdog.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <math.h>

static WebServer server(80);
static TaskHandle_t gHttpTaskHandle = nullptr;
static volatile bool gSelfRestartRequested = false;
static constexpr const char *kAuthHeader = "Authorization";
static constexpr const char *kAuthScheme = "Bearer ";
static constexpr size_t kAuthSchemeLen = 7;
static constexpr size_t kLogSnapshotSize = 64;
static StructuredLog::Entry gLogSnapshot[kLogSnapshotSize]; // static to avoid large stack frames

static void sendAuthFailure(int statusCode, const __FlashStringHelper *message)
{
  JsonDocument doc;
  doc["ok"] = false;
  doc["error"] = message;
  String out;
  serializeJson(doc, out);
  server.sendHeader("WWW-Authenticate", "Bearer realm=\"esp32\"");
  server.send(statusCode, "application/json", out);
}

static bool authorizeRequest()
{
  String configuredKey = AppConfig::get().getHttpApiKey();
  configuredKey.trim();
  if (configuredKey.isEmpty())
  {
    sendAuthFailure(503, F("HTTP API key not configured"));
    return false;
  }

  if (!server.hasHeader(kAuthHeader))
  {
    sendAuthFailure(401, F("Missing Authorization header"));
    return false;
  }

  String presented = server.header(kAuthHeader);
  presented.trim();
  if (presented.startsWith(kAuthScheme))
  {
    presented.remove(0, kAuthSchemeLen);
  }

  if (presented == configuredKey)
  {
    return true;
  }

  sendAuthFailure(401, F("Invalid API key"));
  return false;
}

// Map FreeRTOS state to string
static const char *stateToStr(eTaskState s)
{
  switch (s)
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

static inline char hexDigit(uint8_t nibble)
{
  return (nibble < 10) ? static_cast<char>('0' + nibble) : static_cast<char>('A' + (nibble - 10));
}

static void appendJsonEscaped(String &out, const char *text)
{
  if (!text)
  {
    return;
  }
  while (*text)
  {
    char c = *text++;
    switch (c)
    {
    case '"':
      out += F("\\\"");
      break;
    case '\\':
      out += F("\\\\");
      break;
    case '\b':
      out += F("\\b");
      break;
    case '\f':
      out += F("\\f");
      break;
    case '\n':
      out += F("\\n");
      break;
    case '\r':
      out += F("\\r");
      break;
    case '\t':
      out += F("\\t");
      break;
    default:
      if (static_cast<uint8_t>(c) < 0x20)
      {
        char buf[7];
        buf[0] = '\\';
        buf[1] = 'u';
        buf[2] = '0';
        buf[3] = '0';
        buf[4] = hexDigit((static_cast<uint8_t>(c) >> 4) & 0x0F);
        buf[5] = hexDigit(static_cast<uint8_t>(c) & 0x0F);
        buf[6] = '\0';
        out += buf;
      }
      else
      {
        out += c;
      }
      break;
    }
  }
}

static void handleRoot()
{
  LOG_DEBUG(F("HTTP root request"));
  if (!authorizeRequest())
    return;
  server.send(200, "text/plain", "ok");
}

static void appendMetric(String &out,
                         const __FlashStringHelper *name,
                         const __FlashStringHelper *help,
                         const __FlashStringHelper *type,
                         const String &value)
{
  out += F("# HELP ");
  out += name;
  out += ' ';
  out += help;
  out += '\n';
  out += F("# TYPE ");
  out += name;
  out += ' ';
  out += type;
  out += '\n';
  out += name;
  out += ' ';
  out += value;
  out += '\n';
}

static void handleGetMetrics()
{
  LOG_DEBUG(F("HTTP metrics request"));
  if (!authorizeRequest())
    return;

  MetricsSnapshot snap = Metrics::snapshot();
  String out;
  out.reserve(1024);

  auto appendCounter = [&](const __FlashStringHelper *name, const __FlashStringHelper *help, uint32_t value)
  {
    appendMetric(out, name, help, F("counter"), String(value));
  };
  auto appendGauge = [&](const __FlashStringHelper *name, const __FlashStringHelper *help, const String &value)
  {
    appendMetric(out, name, help, F("gauge"), value);
  };
  auto floatStr = [](float value, uint8_t decimals = 2) -> String
  {
    if (isnan(value))
      return String("nan");
    return String(value, static_cast<unsigned int>(decimals));
  };

  appendCounter(F("esp_sensor_readings_total"), F("Total DHT sensor read attempts"), snap.sensorReadTotal);
  appendCounter(F("esp_sensor_readings_failed_total"), F("Failed DHT sensor reads"), snap.sensorReadFailed);
  appendGauge(F("esp_sensor_read_consecutive_failures"), F("Current consecutive DHT read failures"), String(snap.sensorReadConsecutiveFailures));
  appendGauge(F("esp_last_sensor_read_millis"), F("Millis timestamp of the most recent sensor read attempt"), String(snap.lastSensorReadMillis));
  appendGauge(F("esp_last_sensor_read_success_millis"), F("Millis timestamp of the most recent successful sensor read"), String(snap.lastSensorReadSuccessMillis));
  appendGauge(F("esp_last_temperature_celsius"), F("Most recent temperature reading in Celsius"), floatStr(snap.lastTemperatureC, 2));
  appendGauge(F("esp_last_humidity_percent"), F("Most recent humidity reading (percent)"), floatStr(snap.lastHumidityPct, 2));

  appendCounter(F("esp_post_reading_total"), F("Total attempts to post sensor readings upstream"), snap.postReadingTotal);
  appendCounter(F("esp_post_reading_failed_total"), F("Failed attempts to post sensor readings upstream"), snap.postReadingFailed);
  appendGauge(F("esp_post_reading_consecutive_failures"), F("Current consecutive failed posting attempts for sensor readings"), String(snap.postReadingConsecutiveFailures));
  appendGauge(F("esp_last_post_reading_millis"), F("Millis timestamp of the most recent sensor reading post attempt"), String(snap.lastPostReadingMillis));
  appendGauge(F("esp_last_post_reading_success_millis"), F("Millis timestamp of the most recent successful sensor reading post"), String(snap.lastPostReadingSuccessMillis));

  appendCounter(F("esp_post_error_total"), F("Total attempts to post error payloads upstream"), snap.postErrorTotal);
  appendCounter(F("esp_post_error_failed_total"), F("Failed attempts to post error payloads upstream"), snap.postErrorFailed);
  appendGauge(F("esp_post_error_consecutive_failures"), F("Current consecutive failed error post attempts"), String(snap.postErrorConsecutiveFailures));
  appendGauge(F("esp_last_post_error_millis"), F("Millis timestamp of the most recent error post attempt"), String(snap.lastPostErrorMillis));
  appendGauge(F("esp_last_post_error_success_millis"), F("Millis timestamp of the most recent successful error post"), String(snap.lastPostErrorSuccessMillis));

  appendGauge(F("esp_uptime_millis"), F("Device uptime in milliseconds"), String(snap.uptimeMillis));
  appendGauge(F("esp_heap_free_bytes"), F("Free heap bytes at the time of metrics snapshot"), String(snap.heapFreeBytes));
  appendGauge(F("esp_heap_min_bytes"), F("Minimum observed free heap bytes"), String(snap.heapMinBytes));
  appendGauge(F("esp_wifi_connected"), F("WiFi link status (1=connected,0=disconnected)"), String(snap.wifiConnected ? 1 : 0));
  appendGauge(F("esp_wifi_rssi_dbm"), F("WiFi RSSI in dBm (only valid when connected)"), String(snap.wifiRssiDbm));
  appendCounter(F("esp_wifi_connect_attempts_total"), F("Total WiFi station connection attempts"), snap.wifiConnectAttempts);
  appendCounter(F("esp_wifi_reconnect_events_total"), F("Total times the WiFi link dropped after being established"), snap.wifiReconnectEvents);
  appendGauge(F("esp_wifi_last_attempt_millis"), F("Millis timestamp of the most recent WiFi connect attempt"), String(snap.wifiLastAttemptMillis));
  appendGauge(F("esp_wifi_last_connect_millis"), F("Millis timestamp of the most recent successful WiFi connection"), String(snap.wifiLastConnectedMillis));
  appendGauge(F("esp_wifi_last_disconnect_millis"), F("Millis timestamp of the most recent WiFi disconnect event"), String(snap.wifiLastDisconnectedMillis));
  appendGauge(F("esp_wifi_current_backoff_millis"), F("Current exponential backoff before the next WiFi reconnect attempt"), String(snap.wifiCurrentBackoffMillis));
  appendGauge(F("esp_wifi_connection_duration_millis"), F("Duration in milliseconds of the current WiFi session"), String(snap.wifiConnectionDurationMillis));
  appendGauge(F("esp_wifi_current_attempt_number"), F("Current reconnect attempt sequence number"), String(snap.wifiCurrentAttemptNumber));

  server.send(200, "text/plain; version=0.0.4", out);
}

static void handleGetLogs()
{
  LOG_DEBUG(F("HTTP logs request"));
  if (!authorizeRequest())
    return;

  StructuredLog::Entry *entries = gLogSnapshot;
  size_t count = StructuredLog::snapshot(entries, kLogSnapshotSize);

  String out;
  out.reserve((count * 160) + 64);
  out += F("{\"current_level\":\"");
  out += StructuredLog::levelName(StructuredLog::getLevel());
  out += F("\",\"entries\":[");
  for (size_t i = 0; i < count; ++i)
  {
    if (i > 0)
      out += ',';
    out += F("{\"timestamp_ms\":");
    out += String(entries[i].timestampMs);
    out += F(",\"level\":\"");
    out += StructuredLog::levelName(entries[i].level);
    out += F("\",\"message\":\"");
    appendJsonEscaped(out, entries[i].message);
    out += F("\"}");
  }
  out += F("]}");
  server.send(200, "application/json", out);
}

static void handlePostLogs()
{
  LOG_DEBUG(F("HTTP logs control request"));
  if (!authorizeRequest())
    return;

  if (!server.hasArg("plain"))
  {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing body\"}");
    return;
  }

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err)
  {
    String msg = F("{\"ok\":false,\"error\":\"json error: ");
    msg += err.c_str();
    msg += F("\"}");
    server.send(400, "application/json", msg);
    return;
  }

  bool cleared = false;

  if (!doc["action"].isNull())
  {
    if (!doc["action"].is<const char *>())
    {
      server.send(400, "application/json", "{\"ok\":false,\"error\":\"invalid action\"}");
      return;
    }
    String action = doc["action"].as<String>();
    action.trim();
    action.toLowerCase();
    if (!action.isEmpty())
    {
      if (action == "clear")
      {
        StructuredLog::clear();
        cleared = true;
        StructuredLog::log(StructuredLog::Level::Info, F("Log buffer cleared via HTTP API"));
      }
      else
      {
        server.send(400, "application/json", "{\"ok\":false,\"error\":\"unsupported action\"}");
        return;
      }
    }
  }

  if (!doc["level"].isNull())
  {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"level must be changed via /config\"}");
    return;
  }

  if (!cleared)
  {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"no changes requested\"}");
    return;
  }

  String out;
  out.reserve(64);
  out += F("{\"ok\":true,\"cleared\":");
  out += cleared ? F("true") : F("false");
  out += '}';

  server.send(200, "application/json", out);
}

static void handleGetStatus()
{
  LOG_DEBUG(F("HTTP status request"));
  if (!authorizeRequest())
    return;
  JsonDocument doc;
  doc["wifi_connected"] = (WiFi.status() == WL_CONNECTED);
  doc["ip"] = WiFi.localIP().toString();
  doc["heap_free"] = ESP.getFreeHeap();
  doc["heap_min"] = ESP.getMinFreeHeap();
  doc["uptime_ms"] = millis();

  JsonArray tasks = doc["tasks"].to<JsonArray>();

  // Sensor task
  TaskHandle_t sh = sensorTaskHandle();
  if (sh)
  {
    JsonObject t = tasks.add<JsonObject>();
    t["name"] = "SensorPostTask";
    t["state"] = stateToStr(eTaskGetState(sh));
    t["stack_hwm_words"] = uxTaskGetStackHighWaterMark(sh);
    t["priority"] = uxTaskPriorityGet(sh);
  }
  // HTTP server task (self)
  TaskHandle_t hh = httpServerTaskHandle();
  if (hh)
  {
    JsonObject t = tasks.add<JsonObject>();
    t["name"] = "HttpServerTask";
    t["state"] = stateToStr(eTaskGetState(hh));
    t["stack_hwm_words"] = uxTaskGetStackHighWaterMark(hh);
    t["priority"] = uxTaskPriorityGet(hh);
  }

  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

static void handleGetRead()
{
  LOG_DEBUG(F("HTTP read request"));
  if (!authorizeRequest())
    return;
  float t = NAN, h = NAN;
  String err;
  bool ok = sensorTakeReading(t, h, err);

  JsonDocument doc;
  doc["ok"] = ok;
  doc["location"] = AppConfig::get().getDeviceLocation();
  if (ok)
  {
    doc["temperature_c"] = t;
    doc["humidity_pct"] = h;
  }
  else
  {
    doc["error"] = err;
  }
  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

static void handleGetConfig()
{
  LOG_DEBUG(F("HTTP config request"));
  if (!authorizeRequest())
    return;
  JsonDocument doc;
  AppConfig::get().toJson(doc);
  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

static void handlePostConfig()
{
  LOG_DEBUG(F("HTTP config update"));
  if (!authorizeRequest())
    return;
  if (!server.hasArg("plain"))
  {
    server.send(400, "text/plain", "missing body");
    return;
  }
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err)
  {
    server.send(400, "text/plain", String("json error: ") + err.c_str());
    return;
  }

  // Track WiFi change
  String oldSsid = AppConfig::get().getWifiSSID();
  String oldPass = AppConfig::get().getWifiPassword();
  String oldHostname = AppConfig::get().getWifiHostname();
  String oldMdns = AppConfig::get().getMdnsHostname();
  bool oldStaticEnabled = AppConfig::get().getWifiStaticIpEnabled();
  String oldStaticIp = AppConfig::get().getWifiStaticIp();
  String oldStaticGateway = AppConfig::get().getWifiStaticGateway();
  String oldStaticNetmask = AppConfig::get().getWifiStaticSubnet();
  String oldStaticDns1 = AppConfig::get().getWifiStaticDns1();
  String oldStaticDns2 = AppConfig::get().getWifiStaticDns2();

  AppConfig::get().updateFromJson(doc);

  // Request WiFi reconfigure when any related field changed
  String newSsid = AppConfig::get().getWifiSSID();
  String newPass = AppConfig::get().getWifiPassword();
  String newHostname = AppConfig::get().getWifiHostname();
  String newMdns = AppConfig::get().getMdnsHostname();
  bool newStaticEnabled = AppConfig::get().getWifiStaticIpEnabled();
  String newStaticIp = AppConfig::get().getWifiStaticIp();
  String newStaticGateway = AppConfig::get().getWifiStaticGateway();
  String newStaticNetmask = AppConfig::get().getWifiStaticSubnet();
  String newStaticDns1 = AppConfig::get().getWifiStaticDns1();
  String newStaticDns2 = AppConfig::get().getWifiStaticDns2();

  bool wifiChanged = (newSsid != oldSsid) ||
                     (newPass != oldPass) ||
                     (newHostname != oldHostname) ||
                     (newMdns != oldMdns) ||
                     (newStaticEnabled != oldStaticEnabled) ||
                     (newStaticIp != oldStaticIp) ||
                     (newStaticGateway != oldStaticGateway) ||
                     (newStaticNetmask != oldStaticNetmask) ||
                     (newStaticDns1 != oldStaticDns1) ||
                     (newStaticDns2 != oldStaticDns2);

  if (wifiChanged)
  {
    wifiManagerRequestReconnect(true);
  }

  // Return new config
  JsonDocument outDoc;
  AppConfig::get().toJson(outDoc);
  String out;
  serializeJson(outDoc, out);
  server.send(200, "application/json", out);
}

static void handlePostConfigSave()
{
  LOG_DEBUG(F("HTTP config save"));
  if (!authorizeRequest())
    return;
  bool ok = AppConfig::get().saveToNvs();

  JsonDocument doc;
  doc["ok"] = ok;
  doc["persisted"] = AppConfig::get().hasPersistedConfig();
  JsonObject cfg = doc["config"].to<JsonObject>();
  AppConfig::get().toJson(cfg);

  String out;
  serializeJson(doc, out);
  server.send(ok ? 200 : 500, "application/json", out);
}

static void handlePostConfigDiscard()
{
  LOG_DEBUG(F("HTTP config discard"));
  if (!authorizeRequest())
    return;

  AppConfig::get().loadDefaultsFromMacros();
  bool fromNvs = AppConfig::get().loadFromNvs();

  wifiManagerRequestReconnect(true);

  JsonDocument doc;
  doc["ok"] = true;
  doc["source"] = fromNvs ? "nvs" : "defaults";
  JsonObject cfg = doc["config"].to<JsonObject>();
  AppConfig::get().toJson(cfg);

  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

static void handlePostFactoryReset()
{
  LOG_DEBUG(F("HTTP factory reset"));
  if (!authorizeRequest())
    return;

  bool ok = AppConfig::get().factoryReset();

  wifiManagerRequestReconnect(true);

  JsonDocument doc;
  doc["ok"] = ok;
  doc["persisted"] = AppConfig::get().hasPersistedConfig();
  doc["reboot_recommended"] = true;
  JsonObject cfg = doc["config"].to<JsonObject>();
  AppConfig::get().toJson(cfg);

  String out;
  serializeJson(doc, out);
  server.send(ok ? 200 : 500, "application/json", out);
}

static void handlePostTask()
{
  LOG_DEBUG(F("HTTP task control"));
  if (!authorizeRequest())
    return;
  if (!server.hasArg("plain"))
  {
    server.send(400, "text/plain", "missing body");
    return;
  }
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err)
  {
    server.send(400, "text/plain", String("json error: ") + err.c_str());
    return;
  }
  String name = doc["name"] | "";
  String action = doc["action"] | "";

  if (name.length() == 0 || action.length() == 0)
  {
    server.send(400, "text/plain", "name and action required");
    return;
  }

  bool ok = false;
  if (name == "SensorPostTask")
  {
    TaskHandle_t h = sensorTaskHandle();
    if (action == "suspend" && h)
    {
      vTaskSuspend(h);
      ok = true;
    }
    else if (action == "resume" && h)
    {
      vTaskResume(h);
      ok = true;
    }
    else if (action == "restart")
    {
      restartSensorTask();
      ok = true;
    }
  }
  else if (name == "HttpServerTask")
  {
    TaskHandle_t h = httpServerTaskHandle();
    if (action == "suspend" && h)
    {
      vTaskSuspend(h);
      ok = true;
    }
    else if (action == "resume" && h)
    {
      vTaskResume(h);
      ok = true;
    }
    else if (action == "restart")
    {
      gSelfRestartRequested = true;
      ok = true;
    }
  }

  if (!ok)
  {
    server.send(400, "text/plain", "unsupported task or action");
    return;
  }

  JsonDocument out;
  out["ok"] = true;
  String s;
  serializeJson(out, s);
  server.send(200, "application/json", s);
}

static void HttpTask(void *pv)
{
  LOG_INFO(F("Starting HTTP server..."));
  server.on("/", HTTP_GET, handleRoot);
  server.on("/status", HTTP_GET, handleGetStatus);
  server.on("/read", HTTP_GET, handleGetRead);
  server.on("/config", HTTP_GET, handleGetConfig);
  server.on("/config", HTTP_POST, handlePostConfig);
  server.on("/config/save", HTTP_POST, handlePostConfigSave);
  server.on("/config/discard", HTTP_POST, handlePostConfigDiscard);
  server.on("/config/factory_reset", HTTP_POST, handlePostFactoryReset);
  server.on("/task", HTTP_POST, handlePostTask);
  server.on("/metrics", HTTP_GET, handleGetMetrics);
  server.on("/logs", HTTP_GET, handleGetLogs);
  server.on("/logs", HTTP_POST, handlePostLogs);
  server.begin();
  LOG_INFO(F("HTTP server started on port 80"));

  TaskWatchdog::registerTask(TaskWatchdog::TaskId::HttpServer, "HttpServerTask", restartHttpServerTask, 10000);

  for (;;)
  {
    server.handleClient();
    TaskWatchdog::heartbeat(TaskWatchdog::TaskId::HttpServer);
    if (gSelfRestartRequested)
    {
      gSelfRestartRequested = false;
      TaskWatchdog::unregisterTask(TaskWatchdog::TaskId::HttpServer);
      // respawning a new task to take over loop
      startHttpServerTask();
      vTaskDelete(NULL);
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

void startHttpServerTask()
{
  xTaskCreate(
      HttpTask,
      "HttpServerTask",
      6144,
      nullptr,
      1,
      &gHttpTaskHandle);
}

extern "C"
{
  TaskHandle_t httpServerTaskHandle() { return gHttpTaskHandle; }
  void restartHttpServerTask()
  {
    TaskHandle_t h = gHttpTaskHandle;
    gHttpTaskHandle = nullptr;
    TaskWatchdog::unregisterTask(TaskWatchdog::TaskId::HttpServer);
    if (h)
    {
      vTaskDelete(h);
      vTaskDelay(pdMS_TO_TICKS(10));
    }
    startHttpServerTask();
  }
}
