#pragma once

#include <Arduino.h>
#include <ESPAsyncWebServer.h>
#include <functional>
#include "heating/HeaterTask.h"
#include "heating/ReadyByTask.h"
#include "core/Config.h"
#include "heating/KFactorCalibrator.h"

class WebSocketHub
{
public:
  explicit WebSocketHub(
    AsyncWebServer &server,
    HeaterTask &heaterTask,
    ReadyByTask &readyByTask,
    Config &config,
    KFactorCalibrationManager &calibration);

  // Call once from setup to register /ws handler
  void begin();

  // Broadcast helpers
  void broadcastTimeSync();
  void broadcastLogLine(const String &line);
  void broadcastTempUpdate();
  void broadcastReadyByUpdate();
   void broadcastCalibrationUpdate();

private:
  AsyncWebSocket ws_;
  AsyncWebServer &server_;
  HeaterTask &heaterTask_;
  ReadyByTask &readyByTask_;
  Config &config_;
  KFactorCalibrationManager &calibration_;

  void toggleDeadzone();
  void toggleHeaterTask();
  void toggleHeater();

  void onEvent(AsyncWebSocket *server,
               AsyncWebSocketClient *client,
               AwsEventType type,
               void *arg,
               uint8_t *data,
               size_t len);
};
