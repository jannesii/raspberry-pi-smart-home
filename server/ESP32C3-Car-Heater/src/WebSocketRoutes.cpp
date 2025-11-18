#include "WebSocketRoutes.h"
#include <ArduinoJson.h>

// Single WebSocket endpoint at /ws
static AsyncWebSocket ws("/ws");
static WsToggleCallback g_toggleCb = nullptr;

void wsSetToggleCallback(WsToggleCallback cb)
{
  g_toggleCb = cb;
}

static void onWsEvent(AsyncWebSocket *server,
                      AsyncWebSocketClient *client,
                      AwsEventType type,
                      void *arg,
                      uint8_t *data,
                      size_t len)
{
  switch (type) {
    case WS_EVT_CONNECT:
      Serial.printf("[WS] Client #%u connected from %s\n",
                    client->id(),
                    client->remoteIP().toString().c_str());
      break;

    case WS_EVT_DISCONNECT:
      Serial.printf("[WS] Client #%u disconnected\n", client->id());
      break;

    case WS_EVT_DATA: {
      // If you want to handle messages from browser later, do it here
      AwsFrameInfo *info = (AwsFrameInfo *)arg;
      if (info->final && info->index == 0 && info->len == len && info->opcode == WS_TEXT) {
        String msg;
        msg.reserve(len);
        for (size_t i = 0; i < len; i++) {
          msg += (char)data[i];
        }
        Serial.printf("[WS] Received: %s\n", msg.c_str());
        // (Optional) parse JSON commands here in the future
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

void setupWebSocket(AsyncWebServer &server)
{
  ws.onEvent(onWsEvent);
  server.addHandler(&ws);
}

// Broadcast a single log line as JSON: { "type": "log_append", "line": "..." }
void wsBroadcastLogLine(const String &line)
{
  if (!ws.count()) {
    return; // no clients → skip work
  }

  StaticJsonDocument<256> doc;
  doc["type"] = "log_append";
  doc["line"] = line;

  String json;
  serializeJson(doc, json);
  ws.textAll(json);
}


void wsBroadcastTempUpdate(
    float tempC,
    bool  isOn,
    bool  inDeadzone,
    const String &currentTime
)
{
  if (!ws.count()) {
    return; // nothing connected
  }

  StaticJsonDocument<256> doc;
  doc["type"]         = "temp_update";
  doc["temp"]         = tempC;
  doc["is_on"]        = isOn;
  doc["in_deadzone"]  = inDeadzone;
  doc["current_time"] = currentTime;

  String json;
  serializeJson(doc, json);
  ws.textAll(json);
}