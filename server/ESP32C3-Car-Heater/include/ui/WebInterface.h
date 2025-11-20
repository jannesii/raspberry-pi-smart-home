#pragma once

#include <Arduino.h>
#include <ESPAsyncWebServer.h>

#include "core/Config.h"
#include "heating/Thermostat.h"
#include "io/ShellyHandler.h"
#include "core/LogManager.h"
#include "io/LedManager.h"
#include "heating/HeaterTask.h"
#include "heating/ReadyByTask.h"
#include "heating/KFactorCalibrator.h"

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
               ReadyByTask &readyByTask,
               KFactorCalibrationManager &calibration);

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
  KFactorCalibrationManager &calibration_;

  bool showDebug_ = false; // example tunable

  // setup helpers
  void setupStaticRoutes();
  void setupApiRoutes();
  void setupActionRoutes();

  // handlers
  void handleRoot(AsyncWebServerRequest *request);
  void handleReadyBy(AsyncWebServerRequest *request);
  void handleLogsPage(AsyncWebServerRequest *request);
  void handleCalibratePage(AsyncWebServerRequest *request);
  void handleToggleHttp(AsyncWebServerRequest *request);
  void handleSyncTime(AsyncWebServerRequest *request);
  void handleSetConfig(AsyncWebServerRequest *request);
  void handleLogsClear(AsyncWebServerRequest *request);
  void handleApiStatus(AsyncWebServerRequest *request);
  void handleApiLogs(AsyncWebServerRequest *request);
  void handleReadyByStatus(AsyncWebServerRequest *request);
  void handleReadyBySchedule(AsyncWebServerRequest *request);
  void handleCalibrationStatus(AsyncWebServerRequest *request);
  void handleCalibrationStart(AsyncWebServerRequest *request);
  void handleCalibrationCancel(AsyncWebServerRequest *request);
  void handleCalibrationSettings(AsyncWebServerRequest *request);

  // small internal helper
  String fmtHHMM(uint16_t minutes) const;
};
