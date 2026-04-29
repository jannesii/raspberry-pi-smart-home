#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WebSocketsClient.h>

class WebSocketTask
{
public:
  WebSocketTask();

  void start(uint32_t stackSize = 8192, UBaseType_t priority = 1);
  void stop();

  bool isConnected() const { return connected_; }
  bool isReady() const { return connected_ && authenticated_; }

  struct Stats
  {
    uint32_t connectCount;
    uint32_t disconnectCount;
    uint32_t messagesReceived;
    uint32_t messagesSent;
    uint32_t rpcRequestsHandled;
    uint32_t lastConnectedMs;
    uint32_t uptimeMs;
  };

  Stats getStats() const;
  void queueReading(float temperatureC, float humidityPct);
  void queueError(const String &message);
  void queueStatusUpdate();

  TaskHandle_t handle() const { return handle_; }

private:
  enum class PendingKind : uint8_t
  {
    None = 0,
    Reading = 1,
    Error = 2,
    Status = 3
  };

  static void taskEntry(void *pvParameters);
  static void webSocketEventStatic(WStype_t type, uint8_t *payload, size_t length);

  void run();
  void connect();
  void disconnect();
  void sendAuthentication();
  void sendHeartbeat();
  void sendPendingTelemetry();
  void processMessage(const char *payload, size_t length);
  void handleRpcRequest(JsonVariantConst request);
  void sendRpcResponse(const char *requestId, bool ok, JsonVariantConst data, const char *error = nullptr);
  void sendRpcError(const char *requestId, const char *error);
  void appendStatus(JsonObject target);
  void appendMetrics(JsonObject target);
  void appendLogs(JsonObject target);
  void appendTask(JsonArray tasks, const char *name, TaskHandle_t handle);
  String buildDeviceId();

  WebSocketsClient webSocket_;
  TaskHandle_t handle_ = nullptr;
  SemaphoreHandle_t pendingMutex_ = nullptr;
  volatile bool running_ = false;
  volatile bool connected_ = false;
  volatile bool authenticated_ = false;

  PendingKind pendingKind_ = PendingKind::None;
  float pendingTemperatureC_ = NAN;
  float pendingHumidityPct_ = NAN;
  String pendingError_;

  String host_;
  String path_;
  uint16_t port_ = 443;
  bool useTls_ = true;

  uint32_t reconnectDelayMs_ = 1000;
  static constexpr uint32_t RECONNECT_DELAY_MIN_MS = 1000;
  static constexpr uint32_t RECONNECT_DELAY_MAX_MS = 60000;
  static constexpr uint32_t HEARTBEAT_INTERVAL_MS = 30000;
  uint32_t lastHeartbeatMs_ = 0;

  Stats stats_ = {};
  static WebSocketTask *instance_;
};
