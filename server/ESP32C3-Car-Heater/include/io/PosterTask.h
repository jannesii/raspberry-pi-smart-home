#pragma once

#include <Arduino.h>
#include <functional>
#include <HTTPClient.h>

#include "io/ShellyHandler.h"
#include "core/LogManager.h"
#include <core/staticconfig.h>

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
    void sleepUntilNextSlot();
    void processServerCommands(const String &respBody);
    void handleTurnOn();
    void handleTurnOff();
    void handleGetLogs();
    void handleEspRestart();
    void handleShellyReboot();
    void handlePostDelay(uint32_t seconds);
    void handleUnknownAction(const char* action);

    void sendImmediateResultIfNeeded();

    ShellyHandler &shelly_;
    LogManager &logger_;

    TaskHandle_t handle_ = nullptr;

    HTTPClient http_;          // persistent HTTP client
    bool httpInitialized_ = false;

    void initHttpIfNeeded();
    
    uint32_t taskDelayS_ = 5;
    uint32_t postCount_ = 0;
    float avgPostMs_ = 0.0f;
    
    String apiURL_ = "https://jannenkoti.com/api/car_heater/status";
    String apiKey_ = API_KEY;

    struct ActionResult {
        String action;
        bool success;
        String note;
    };

    static constexpr size_t kMaxPendingActions = 8;

    ActionResult pendingActions_[kMaxPendingActions];
    size_t pendingActionCount_ = 0;

    String pendingLogs_;   // logs to send on next post

    bool espRestartPending_ = false;
    bool espRestartResultSent_ = false;

    uint32_t wifiDisconnectCount_ = 0;
    static constexpr uint32_t WIFI_MAX_DISCONNECT_LOOPS = 12; // e.g. 12 loops

    void queueActionResult(const char *action, bool success, const String &note = "");
};
