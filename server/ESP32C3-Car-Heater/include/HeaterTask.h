#pragma once

#include <Arduino.h>
#include <functional>

#include "Config.h"
#include "Thermostat.h"
#include "ShellyHandler.h"
#include "LogManager.h"
#include "LedManager.h"

// Wrapper around the FreeRTOS heater control task
class HeaterTask
{
public:
    using KickCallback = std::function<void(void)>;
    using wsTempUpdateCallback = std::function<void()>;

    HeaterTask(Config &config,
               Thermostat &thermostat,
               ShellyHandler &shelly,
               LogManager &logManager,
               LedManager &led);

    // Create and start the FreeRTOS task
    void start(uint32_t stackSize = 4096, UBaseType_t priority = 1);

    TaskHandle_t handle() const { return handle_; }

    // Optional: set a callback that is called each loop iteration
    // (used for watchdog kick or similar)
    void setKickCallback(KickCallback cb) { kickCallback_ = cb; }

    void setWsTempUpdateCallback(wsTempUpdateCallback cb) { wsTempUpdateCallback_ = cb; }

    void setEnabled(bool enabled) { enabled_ = enabled; }
    bool isEnabled() const { return enabled_; }

    bool isInDeadzone() const;
    void setDeadzoneEnabled(bool enabled) { dzEnabled_ = enabled; }
    bool isDeadzoneEnabled() const { return dzEnabled_; }

    float currentTemp() const { return currentTemp_; }
    bool isHeaterOn() const { return isHeaterOn_; }

    bool turnHeaterOn(bool force = false);
    bool turnHeaterOff();

private:
    // Task entry trampoline
    static void taskEntry(void *pvParameters);

    // Actual task loop
    void run();

    // Helpers
    String logHeaterChange(bool isOn, float currentTemp) const;
    String logDZChange(bool inDZ) const;

    Config &config_;
    Thermostat &thermostat_;
    ShellyHandler &shelly_;
    LogManager &logger_;
    LedManager &led_;

    TaskHandle_t handle_ = nullptr;
    bool lastInDeadzone_ = false;
    bool enabled_ = true;
    bool dzEnabled_ = true;

    float currentTemp_;
    bool isHeaterOn_;

    KickCallback kickCallback_{nullptr};
    wsTempUpdateCallback wsTempUpdateCallback_{nullptr};
};
