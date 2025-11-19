#pragma once

#include <Arduino.h>
#include <ESPAsyncWebServer.h>
#include <functional>

class WebSocketHub
{
public:
  using HeaterToggleCallback = std::function<void(void)>;

  explicit WebSocketHub(AsyncWebServer &server);

  // Call once from setup to register /ws handler
  void begin();

  // Set callback to run when "toggle_heater" WS command is received
  void setHeaterToggleCallback(HeaterToggleCallback cb) { heaterToggleCb_ = std::move(cb); }

  // Broadcast helpers
  void broadcastLogLine(const String &line);
  void broadcastTempUpdate(float tempC,
                           bool isOn,
                           bool inDeadzone,
                           const String &currentTime);

private:
  AsyncWebSocket ws_;
  AsyncWebServer &server_;
  HeaterToggleCallback heaterToggleCb_{nullptr};

  void onEvent(AsyncWebSocket *server,
               AsyncWebSocketClient *client,
               AwsEventType type,
               void *arg,
               uint8_t *data,
               size_t len);
};
