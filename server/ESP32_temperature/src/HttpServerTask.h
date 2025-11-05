#pragma once

#include <Arduino.h>

void startHttpServerTask();

// Expose handle/control for external use
extern "C" {
  TaskHandle_t httpServerTaskHandle();
  void restartHttpServerTask();
}

