// WatchDog.cpp
#include "WatchDog.h"
#include "Config.h"
#include "Thermostat.h"
#include "ShellyHandler.h"
#include "LogManager.h"
#include "HeaterTask.h" // for g_heaterTaskHandle and startHeaterTask
#include "timekeeper.h"
#include "LedManager.h"
#include <esp_system.h> // esp_restart()

String logHeaterRestart();
String logESPRestart(bool dueToHeater);
String logWifiReconnectAttempt();
String logShellyReconnectAttempt();
String logShellyRestart();

WatchDog::WatchDog(Config &config, Thermostat &thermostat, ShellyHandler &shelly, LogManager &logManager, LedManager &led, HeaterTask &heaterTask)
    : config_(config),
      thermostat_(thermostat),
      shelly_(shelly),
      logManager_(logManager),
      led_(led),
      heaterTask_(heaterTask)
{}

void WatchDog::begin(uint32_t stackSize, UBaseType_t priority)
{
    xTaskCreate(
        &WatchDog::taskEntryPoint,
        "WatchDog",
        stackSize,
        this,
        priority, // slightly higher prio than heater if you want
        &taskHandle_);
}

void WatchDog::taskEntryPoint(void *param)
{
    auto *self = static_cast<WatchDog *>(param);
    self->taskLoop();
}

void WatchDog::kickHeater()
{
    lastHeaterKickTick_ = xTaskGetTickCount();
    taskRestartAttempts_ = 0; // successful activity → reset retry counter
}

void WatchDog::taskLoop()
{
    const TickType_t checkIntervalTicks = pdMS_TO_TICKS(5000); // every 5s

    lastHeaterKickTick_ = xTaskGetTickCount();

    for (;;)
    {
        checkWifi();
        checkHeater();
        checkShelly();

        vTaskDelay(checkIntervalTicks);
    }
}

void WatchDog::checkShelly()
{
    if (shelly_.ping())
        return; // all good

    shellyReconnectAttempts_++;
    if (shellyReconnectAttempts_ <= MAX_RESTART_ATTEMPTS)
    {
        Serial.printf("[WatchDog] Shelly not reachable, attempt to reconnect... (attempt %u)\n", shellyReconnectAttempts_);
        WiFi.reconnect();
        logManager_.append(logShellyReconnectAttempt());
        led_.blinkTriple();
        return;
    }
    Serial.println(F("[WatchDog] Max Shelly reconnect attempts reached, restarting Shelly..."));
    shelly_.reboot();
    shellyReconnectAttempts_ = 0; // reset counter after reboot attempt
    logManager_.append(logShellyRestart());
    led_.rapidBurst();
}

void WatchDog::checkWifi()
{
    wl_status_t st = WiFi.status();
    if (st == WL_CONNECTED)
    {
        return; // all good
    }
    wifiReconnectAttempts_++;
    if (wifiReconnectAttempts_ <= MAX_RESTART_ATTEMPTS)
    {
        Serial.printf("[WatchDog] WiFi disconnected, trying to reconnect... (attempt %u)\n", wifiReconnectAttempts_);
        WiFi.reconnect();
        logManager_.append(logWifiReconnectAttempt());
        led_.blinkDouble();
        return;
    }
    Serial.println(F("[WatchDog] Max WiFi reconnect attempts reached, restarting ESP..."));
    logManager_.append(logESPRestart(false));
    led_.rapidBurst();
    esp_restart();
}

void WatchDog::checkHeater()
{
    // If heater task hasn't been created yet — nothing to do
    TaskHandle_t heaterTaskHandle = heaterTask_.handle();
    if (heaterTaskHandle == nullptr)
    {
        return;
    }

    // If we never got a kick yet, don't judge
    if (lastHeaterKickTick_ == 0)
    {
        return;
    }

    TickType_t now = xTaskGetTickCount();

    // Consider heater stuck if no kick for 3× its normal period
    uint32_t delayMs = static_cast<uint32_t>(config_.heaterTaskDelayS() * 1000.0f);
    TickType_t timeoutTicks = pdMS_TO_TICKS(delayMs * 3);

    TickType_t elapsed = now - lastHeaterKickTick_;

    if (elapsed <= timeoutTicks)
    {
        return; // heater looks alive
    }

    // At this point, heater looks stuck
    taskRestartAttempts_++;

    Serial.printf("[WatchDog] Heater task stuck (attempt %u/%u)\n",
                  taskRestartAttempts_, MAX_RESTART_ATTEMPTS);

    // Try restart if we still have attempts left
    if (taskRestartAttempts_ <= MAX_RESTART_ATTEMPTS)
    {
        Serial.println(F("[WatchDog] Restarting heater task..."));

        // Kill current task
        vTaskDelete(heaterTaskHandle);
        heaterTaskHandle = nullptr;

        // Reset kick timer to now so we give new task a fresh window
        lastHeaterKickTick_ = xTaskGetTickCount();

        // Recreate heater task with same dependencies
        heaterTask_.start(4096, 1); // stack size, priority
        logManager_.append(logHeaterRestart());
        led_.rapidBurst();
        return;
    }

    // Too many restarts → full system reboot
    Serial.println(F("[WatchDog] Max heater restarts reached, restarting ESP..."));
    logManager_.append(logESPRestart(true));
    led_.rapidBurst();
    esp_restart();
}

String logHeaterRestart()
{
    String line;
    line.reserve(64);
    line += timekeeper::formatLocal();
    line += " - WatchDog: Heater task restarted";
    return line;
}
String logESPRestart(bool dueToHeater)
{
    String line;
    line.reserve(64);
    line += timekeeper::formatLocal();
    if (dueToHeater)
    {
        line += " - WatchDog: ESP restarted due to heater task failure";
    }
    else
    {
        line += " - WatchDog: ESP restarted due to WiFi/Shelly failure";
    }
    return line;
}
String logWifiReconnectAttempt()
{
    String line;
    line.reserve(64);
    line += timekeeper::formatLocal();
    line += " - WatchDog: WiFi reconnect attempt";
    return line;
}
String logShellyReconnectAttempt()
{
    String line;
    line.reserve(64);
    line += timekeeper::formatLocal();
    line += " - WatchDog: Shelly reconnect attempt";
    return line;
}
String logShellyRestart()
{
    String line;
    line.reserve(64);
    line += timekeeper::formatLocal();
    line += " - WatchDog: Shelly restarted";
    return line;
}
