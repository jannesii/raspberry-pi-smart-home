#include "HttpServerTask.h"

#include <WebServer.h>
#include <WiFi.h>

#include <ArduinoJson.h>

#include "AppConfig.h"
#include "SensorTask.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static WebServer server(80);
static TaskHandle_t gHttpTaskHandle = nullptr;
static volatile bool gSelfRestartRequested = false;

// Map FreeRTOS state to string
static const char* stateToStr(eTaskState s) {
  switch (s) {
    case eRunning: return "running";
    case eReady: return "ready";
    case eBlocked: return "blocked";
    case eSuspended: return "suspended";
    case eDeleted: return "deleted";
    default: return "unknown";
  }
}

static void handleRoot() {
  server.send(200, "text/plain", "ok");
}

static void handleGetStatus() {
  DynamicJsonDocument doc(1024);
  doc["wifi_connected"] = (WiFi.status() == WL_CONNECTED);
  doc["ip"] = WiFi.localIP().toString();
  doc["heap_free"] = ESP.getFreeHeap();
  doc["heap_min"] = ESP.getMinFreeHeap();
  doc["uptime_ms"] = millis();

  JsonArray tasks = doc.createNestedArray("tasks");

  // Sensor task
  TaskHandle_t sh = sensorTaskHandle();
  if (sh) {
    JsonObject t = tasks.createNestedObject();
    t["name"] = "SensorPostTask";
    t["state"] = stateToStr(eTaskGetState(sh));
    t["stack_hwm_words"] = uxTaskGetStackHighWaterMark(sh);
    t["priority"] = uxTaskPriorityGet(sh);
  }
  // HTTP server task (self)
  TaskHandle_t hh = httpServerTaskHandle();
  if (hh) {
    JsonObject t = tasks.createNestedObject();
    t["name"] = "HttpServerTask";
    t["state"] = stateToStr(eTaskGetState(hh));
    t["stack_hwm_words"] = uxTaskGetStackHighWaterMark(hh);
    t["priority"] = uxTaskPriorityGet(hh);
  }

  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

static void handleGetRead() {
  float t = NAN, h = NAN;
  String err;
  bool ok = sensorTakeReading(t, h, err);

  DynamicJsonDocument doc(256);
  doc["ok"] = ok;
  doc["location"] = AppConfig::get().getDeviceLocation();
  if (ok) {
    doc["temperature_c"] = t;
    doc["humidity_pct"] = h;
  } else {
    doc["error"] = err;
  }
  String out; serializeJson(doc, out);
  server.send(200, "application/json", out);
}

static void handleGetConfig() {
  DynamicJsonDocument doc(1024);
  AppConfig::get().toJson(doc);
  String out; serializeJson(doc, out);
  server.send(200, "application/json", out);
}

static void handlePostConfig() {
  if (!server.hasArg("plain")) {
    server.send(400, "text/plain", "missing body");
    return;
  }
  DynamicJsonDocument doc(1024);
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err) {
    server.send(400, "text/plain", String("json error: ") + err.c_str());
    return;
  }

  // Track WiFi change
  String oldSsid = AppConfig::get().getWifiSSID();
  String oldPass = AppConfig::get().getWifiPassword();

  AppConfig::get().updateFromJson(doc);

  // Apply WiFi changes immediately if credentials changed
  String newSsid = AppConfig::get().getWifiSSID();
  String newPass = AppConfig::get().getWifiPassword();
  if (newSsid != oldSsid || newPass != oldPass) {
    WiFi.disconnect(true);
    WiFi.begin(newSsid.c_str(), newPass.c_str());
  }

  // Return new config
  DynamicJsonDocument outDoc(1024);
  AppConfig::get().toJson(outDoc);
  String out; serializeJson(outDoc, out);
  server.send(200, "application/json", out);
}

static void handlePostTask() {
  if (!server.hasArg("plain")) {
    server.send(400, "text/plain", "missing body");
    return;
  }
  DynamicJsonDocument doc(512);
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err) { server.send(400, "text/plain", String("json error: ") + err.c_str()); return; }
  String name = doc["name"] | "";
  String action = doc["action"] | "";

  if (name.length() == 0 || action.length() == 0) {
    server.send(400, "text/plain", "name and action required");
    return;
  }

  bool ok = false;
  if (name == "SensorPostTask") {
    TaskHandle_t h = sensorTaskHandle();
    if (action == "suspend" && h) { vTaskSuspend(h); ok = true; }
    else if (action == "resume" && h) { vTaskResume(h); ok = true; }
    else if (action == "restart") { restartSensorTask(); ok = true; }
  } else if (name == "HttpServerTask") {
    TaskHandle_t h = httpServerTaskHandle();
    if (action == "suspend" && h) { vTaskSuspend(h); ok = true; }
    else if (action == "resume" && h) { vTaskResume(h); ok = true; }
    else if (action == "restart") { gSelfRestartRequested = true; ok = true; }
  }

  if (!ok) {
    server.send(400, "text/plain", "unsupported task or action");
    return;
  }

  DynamicJsonDocument out(256);
  out["ok"] = true;
  String s; serializeJson(out, s);
  server.send(200, "application/json", s);
}

static void HttpTask(void* pv) {
  server.on("/", HTTP_GET, handleRoot);
  server.on("/status", HTTP_GET, handleGetStatus);
  server.on("/read", HTTP_GET, handleGetRead);
  server.on("/config", HTTP_GET, handleGetConfig);
  server.on("/config", HTTP_POST, handlePostConfig);
  server.on("/task", HTTP_POST, handlePostTask);
  server.begin();
  Serial.println(F("HTTP server started on port 80"));

  for (;;) {
    server.handleClient();
    if (gSelfRestartRequested) {
      gSelfRestartRequested = false;
      // respawning a new task to take over loop
      startHttpServerTask();
      vTaskDelete(NULL);
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

void startHttpServerTask() {
  xTaskCreate(
      HttpTask,
      "HttpServerTask",
      6144,
      nullptr,
      1,
      &gHttpTaskHandle);
}

extern "C" {
  TaskHandle_t httpServerTaskHandle() { return gHttpTaskHandle; }
  void restartHttpServerTask() {
    TaskHandle_t h = gHttpTaskHandle;
    gHttpTaskHandle = nullptr;
    if (h) {
      vTaskDelete(h);
      vTaskDelay(pdMS_TO_TICKS(10));
    }
    startHttpServerTask();
  }
}
