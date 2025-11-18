#pragma once

#include <Arduino.h>

#include "Config.h"
#include "Thermostat.h"
#include "ShellyHandler.h"
#include "LogManager.h"
#include <WatchDog.h>

class LedManager; // forward decl

extern TaskHandle_t g_heaterTaskHandle;

void heaterTask(void *args);

void startHeaterTask(
    Config &config,
    Thermostat &thermostat,
    ShellyHandler &shelly,
    LogManager &logManager,
    WatchDog &watchdog,
    LedManager &led
);
