#pragma once

#include <Arduino.h>
#include <ESPAsyncWebServer.h>

#include "Config.h"
#include "Thermostat.h"
#include "ShellyHandler.h"
#include "LogManager.h"
#include "LedManager.h"
#include "HeaterTask.h"
#include "ReadyByTask.h"

// forward declare helper if you keep it free, or move into class
class WebInterface
{
public:
  WebInterface(AsyncWebServer &server,
               Config &config,
               Thermostat &thermostat,
               ShellyHandler &shelly,
               LogManager &logManager,
               const String &wifiSSID,
               LedManager &led,
               HeaterTask &heaterTask,
               ReadyByTask &readyByTask);

  // Call once from setup() after WiFi + FS are ready
  void begin();

  // example future “variables you might want to tweak”:
  void setShowDebug(bool enabled) { showDebug_ = enabled; }
  bool showDebug() const { return showDebug_; }

private:
  AsyncWebServer &server_;
  Config &config_;
  Thermostat &thermostat_;
  ShellyHandler &shelly_;
  LogManager &logManager_;
  String wifiSSID_;
  LedManager &led_;
  HeaterTask &heaterTask_;
  ReadyByTask &readyByTask_;

  bool showDebug_ = false; // example tunable

  // setup helpers
  void setupStaticRoutes();
  void setupApiRoutes();
  void setupActionRoutes();

  // handlers
  void handleRoot(AsyncWebServerRequest *request);
  void handleReadyBy(AsyncWebServerRequest *request);
  void handleLogsPage(AsyncWebServerRequest *request);
  void handleToggleHttp(AsyncWebServerRequest *request);
  void handleSyncTime(AsyncWebServerRequest *request);
  void handleSetConfig(AsyncWebServerRequest *request);
  void handleLogsClear(AsyncWebServerRequest *request);
  void handleApiStatus(AsyncWebServerRequest *request);
  void handleApiLogs(AsyncWebServerRequest *request);
  void handleReadyByStatus(AsyncWebServerRequest *request);
  void handleReadyBySchedule(AsyncWebServerRequest *request);

  // small internal helper
  String fmtHHMM(uint16_t minutes) const;
};
