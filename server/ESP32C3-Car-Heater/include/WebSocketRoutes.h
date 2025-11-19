#pragma once

#include <Arduino.h>
#include <ESPAsyncWebServer.h>
#include <functional>

// Attach the /ws endpoint to the server
void setupWebSocket(AsyncWebServer &server);

// Broadcast a single new log line to all connected WS clients
void wsBroadcastLogLine(const String &line);

// Broadcast a temperature / heater status update
void wsBroadcastTempUpdate(
    float tempC,
    bool  isOn,
    bool  inDeadzone,
    const String &currentTime
);

// App can register a callback for "toggle_heater" command from WS
using WsToggleCallback = std::function<void(void)>;
void wsSetToggleCallback(WsToggleCallback cb);
