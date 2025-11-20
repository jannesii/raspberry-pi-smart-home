#pragma once

#include <Arduino.h>
#include "core/Config.h"
#include "heating/HeaterTask.h"
#include "core/LogManager.h"
#include "heating/Thermostat.h"

class KFactorCalibrationManager;

class ReadyByTask
{
public:
    using wsReadyByUpdateCallback = std::function<void()>;

    ReadyByTask(Config &config,
                HeaterTask &heaterTask,
                LogManager &logManager,
                Thermostat &thermostat);

    // Create and start the FreeRTOS task
    void start(uint32_t stackSize = 4096, UBaseType_t priority = 1);

    void setCalibrationManager(KFactorCalibrationManager *mgr) { calibMgr_ = mgr; }

    void setWsReadyByUpdateCallback(wsReadyByUpdateCallback callback) 
    { wsReadyByUpdateCallback_ = callback; }

    // Schedule a new "ready by" event.
    // targetEpochUtc: desired time (UTC epoch seconds) when cabin should be at targetTempC.
    void schedule(uint64_t targetEpochUtc, float targetTempC);
    bool getSchedule(uint64_t &targetEpochUtc, float &targetTempC) const;

    // Cancel any scheduled event; also turns off forced heating if active.
    void cancel();

    bool isActive() const { return active_; }

    void stop()
    {
        if (handle_ != nullptr)
        {
            vTaskDelete(handle_);
            handle_ = nullptr;
        }
    }

private:
    // Task entry trampoline
    static void taskEntry(void *pvParameters);

    // Actual task loop
    void run();

    // internal helpers
    void clearScheduleLocked();   // used from task context
    void logScheduleInfo(const char *msgPrefix) const;
    String log(const String &msg) const;
    void exitActions();

    Config             &config_;
    HeaterTask         &heaterTask_;
    LogManager         &logManager_;
    Thermostat         &thermostat_;
    KFactorCalibrationManager *calibMgr_ = nullptr;

    TaskHandle_t handle_ = nullptr;

    // Shared state between main code and task.
    // Written in schedule()/cancel(), read in run().
    // We keep writes ordered so "active_" flips last to avoid half-written state.
    volatile bool     active_        = false;
    volatile bool     heatingForced_ = false;
    volatile uint64_t targetEpochUtc_ = 0;
    volatile float    targetTempC_    = 0.0f;
    volatile bool     targetTempReached_ = false;

    wsReadyByUpdateCallback wsReadyByUpdateCallback_{nullptr};
};
