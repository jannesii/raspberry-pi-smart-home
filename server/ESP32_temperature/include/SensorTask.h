#pragma once

#include <Arduino.h>

class WebSocketTask;

// Starts the sensor reading task.
// Pass a pointer to a global/static WebSocketTask instance.
void startSensorTask(WebSocketTask *webSocketTask);

// Expose handle/control for server
extern "C" {
  TaskHandle_t sensorTaskHandle();
  void restartSensorTask();
}

// Take an immediate DHT reading (thread-safe) without posting.
// Returns true on success and fills temperatureC/humidityPct; false with errorOut.
bool sensorTakeReading(float &temperatureC, float &humidityPct, String &errorOut);
