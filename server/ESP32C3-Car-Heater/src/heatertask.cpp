#include "heatertask.h"
#include "measurements.h"
#include "timekeeper.h"
#include "LedManager.h"

TaskHandle_t g_heaterTaskHandle = nullptr;

struct HeaterTaskArgs {
    Config*       config;
    Thermostat*   thermostat;
    ShellyHandler* shelly;
    LogManager*   logManager;
    WatchDog*     watchdog;
    LedManager*   led;
};

struct Deadzone {
    uint16_t startMin;  // [0..1439]
    uint16_t endMin;    // [0..1439]
};

bool isInDeadzone(const Deadzone& dz);
String logHeaterChange(bool isOn, float currentTemp, float targetTemp);
String logDZChange(bool inDZ);

void heaterTask(void *args) {
    auto* taskArgs = static_cast<HeaterTaskArgs*>(args);
    Config* config = taskArgs->config;
    Thermostat* thermostat = taskArgs->thermostat;
    ShellyHandler* shelly = taskArgs->shelly;
    LogManager* logger = taskArgs->logManager;
    WatchDog* watchdog     = taskArgs->watchdog;
    LedManager* led        = taskArgs->led;

    bool lastInDeadzone = isInDeadzone(Deadzone{
            config->deadzoneStartMin(),
            config->deadzoneEndMin()
    });

    bool isOn;
    for (;;) {
        bool success = shelly->getStatus(isOn, false);
        float currentTemp = takeMeasurement(false).temperature;
        bool shouldHeat = thermostat->update(currentTemp);
        bool inDeadzone = isInDeadzone(Deadzone{
            config->deadzoneStartMin(),
            config->deadzoneEndMin()
        });
        if (inDeadzone != lastInDeadzone) {
            logger->append(logDZChange(inDeadzone));
            lastInDeadzone = inDeadzone;
        }
        if (inDeadzone) {
            shouldHeat = false;
        }
        if (shouldHeat && !isOn) {
            shelly->switchOn();
            logger->append(logHeaterChange(true, currentTemp, config->targetTemp()));
            if (led) led->blinkSingle();
        } else if (!shouldHeat && isOn) {
            shelly->switchOff();
            logger->append(logHeaterChange(false, currentTemp, config->targetTemp()));
            if (led) led->blinkSingle();
        }

        // Tell the watchdog "I am alive"
        if (watchdog) {
            watchdog->kickHeater();
        }

        vTaskDelay(pdMS_TO_TICKS(config->heaterTaskDelayS() * 1000) );
    }

    // never reached, but if you ever add an exit, you'd delete taskArgs
}

void startHeaterTask(
    Config &config,
    Thermostat &thermostat,
    ShellyHandler &shelly,
    LogManager &logManager,
    WatchDog &watchdog,
    LedManager &led
) {
    auto* args = new HeaterTaskArgs{ 
        &config, 
        &thermostat, 
        &shelly, 
        &logManager,
        &watchdog,
        &led
    };

    xTaskCreate(
        heaterTask,
        "HeaterTask",
        4096,
        args,
        1,
        &g_heaterTaskHandle
    );
    Serial.println("[HeaterTask] Started heater task");
}

bool isInDeadzone(const Deadzone& dz) {
    if (!timekeeper::isValid()) {
        return false;  // or your choice: treat “no time” as “no deadzone”
    }

    int m = timekeeper::localMinutesOfDay();
    if (m < 0) return false;  // time invalid

    // Normal range, e.g. 08:00–17:00
    if (dz.startMin <= dz.endMin) {
        return m >= dz.startMin && m < dz.endMin;
    }

    // Wrap-around range, e.g. 20:00–06:00
    // Example: [1200, 360] → 20:00..24:00 or 00:00..06:00
    return (m >= dz.startMin) || (m < dz.endMin);
}


String logHeaterChange(bool isOn, float currentTemp, float targetTemp) {
    String line;
    line.reserve(60);
    line += timekeeper::formatLocal();     // "YYYY-MM-DD HH:MM:SS"
    line += isOn ? " Heater turned ON" : " Heater turned OFF";
    line += " | Current: ";
    line += String(currentTemp, 1);
    line += "°C Target: ";
    line += String(targetTemp, 1);
    line += "°C";
    return line;
}
String logDZChange(bool inDZ) {
    String line;
    line.reserve(60);
    line += timekeeper::formatLocal();     // "YYYY-MM-DD HH:MM:SS"
    line += inDZ ? " Entered deadzone" : " Exited deadzone";
    return line;
}
