#pragma once

#include <Arduino.h>
#include <ESPAsyncWebServer.h>
#include <functional>
#include "HeaterTask.h"

class WebSocketHub
{
public:
  explicit WebSocketHub(
    AsyncWebServer &server,
    HeaterTask &heaterTask);

  // Call once from setup to register /ws handler
  void begin();

  // Broadcast helpers
  void broadcastLogLine(const String &line);
  void broadcastTempUpdate();

private:
  AsyncWebSocket ws_;
  AsyncWebServer &server_;
  HeaterTask &heaterTask_;

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
