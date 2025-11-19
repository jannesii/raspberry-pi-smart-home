// WebRoutes.h
#pragma once
#include <ESPAsyncWebServer.h>
class Config;
class Thermostat;
class ShellyHandler;
class LogManager;
class LedManager;

void setupRoutes(AsyncWebServer &server,
                 Config &config,
                 Thermostat &thermostat,
                 ShellyHandler &shelly,
                 LogManager &logManager,
                 String wifiSSID,
                 LedManager &led
                );

static String fmtHHMM(uint16_t minutes);
