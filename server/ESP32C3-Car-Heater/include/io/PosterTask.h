#pragma once

#include <Arduino.h>
#include <functional>

#include "io/ShellyHandler.h"
#include "core/LogManager.h"

// Wrapper around the FreeRTOS heater control task
class PosterTask
{
public:
    PosterTask(ShellyHandler &shelly,
               LogManager &logManager);

    // Create and start the FreeRTOS task
    void start(uint32_t stackSize = 4096, UBaseType_t priority = 1);

    TaskHandle_t handle() const { return handle_; }

    void stop()
    {
        if (handle_ != nullptr)
        {
            log("Heater task stopped");
            vTaskDelete(handle_);
            handle_ = nullptr;
        }
    }

private:
    // Task entry trampoline
    static void taskEntry(void *pvParameters);

    // Actual task loop
    void run();

    // Helpers
    String log(const String &msg) const;

    ShellyHandler &shelly_;
    LogManager &logger_;

    TaskHandle_t handle_ = nullptr;

    float taskDelayS_ = 10.0f;
    uint32_t postCount_ = 0;
    float avgPostMs_ = 0.0f;

    String apiURL_ = "https://jannenkoti.com/api/car_heater/status";
};
