// WatchDog.h
#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

class Config;
class Thermostat;
class ShellyHandler;
class LogManager;
class LedManager;
class HeaterTask;

class WatchDog
{
public:
    WatchDog(
        Config &config,
        Thermostat &thermostat,
        ShellyHandler &shelly,
        LogManager &logManager,
        LedManager &led,
        HeaterTask &heaterTask
    );
    void begin(uint32_t stackSize = 4096, UBaseType_t priority = 2);      // create watchdog task
    void kickHeater(); // called by heaterTask when it runs OK

private:
    static void taskEntryPoint(void *param);
    void taskLoop();

    void checkWifi();
    void checkHeater();
    void checkShelly();

    Config &config_;
    Thermostat &thermostat_;
    ShellyHandler &shelly_;
    LogManager &logManager_;
    LedManager &led_;
    HeaterTask &heaterTask_;

    TaskHandle_t taskHandle_ = nullptr;

    // last time (ticks) heater kicked us
    volatile uint32_t lastHeaterKickTick_ = 0;

    // restart logic
    uint8_t taskRestartAttempts_ = 0;
    uint8_t wifiReconnectAttempts_ = 0;
    uint8_t shellyReconnectAttempts_ = 0;
    static constexpr uint8_t MAX_RESTART_ATTEMPTS = 3;
};
