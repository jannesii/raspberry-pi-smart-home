#include "HeaterTask.h"

#include "measurements.h"
#include "timekeeper.h"
#include "WebSocketHub.h"

HeaterTask::HeaterTask(Config &config,
                       Thermostat &thermostat,
                       ShellyHandler &shelly,
                       LogManager &logManager,
                       LedManager &led)
    : config_(config),
      thermostat_(thermostat),
      shelly_(shelly),
      logger_(logManager),
      led_(led)
{
}

void HeaterTask::start(uint32_t stackSize, UBaseType_t priority)
{
    // Initialize lastInDeadzone_ before starting
    lastInDeadzone_ = isInDeadzone();

    xTaskCreate(
        &HeaterTask::taskEntry,
        "HeaterTask",
        stackSize,
        this, // pass this as pvParameters
        priority,
        &handle_);

    Serial.println("[HeaterTask] Started heater task");
}

void HeaterTask::taskEntry(void *pvParameters)
{
    auto *self = static_cast<HeaterTask *>(pvParameters);
    self->run();
    // never returns
}

void HeaterTask::run()
{
    for (;;)
    {
        bool success = shelly_.getStatus(isHeaterOn_, false);
        (void)success; // you can log or handle failure if you want

        currentTemp_ = takeMeasurement(false).temperature;
        bool shouldHeat = thermostat_.update(currentTemp_);

        bool inDeadzone = isInDeadzone();
        if (inDeadzone != lastInDeadzone_)
        {
            logger_.append(logDZChange(inDeadzone));
            lastInDeadzone_ = inDeadzone;
        }

        if (inDeadzone && dzEnabled_)
        {
            shouldHeat = false;
        }

        if (enabled_)
        {
            if (shouldHeat && !isHeaterOn_)
            {
                turnHeaterOn();
                logger_.append(logHeaterChange(true, currentTemp_));
                led_.blinkSingle();
            }
            else if (!shouldHeat && isHeaterOn_)
            {
                turnHeaterOff();
                logger_.append(logHeaterChange(false, currentTemp_));
                led_.blinkSingle();
            }
        }

        // Tell the watchdog "I am alive" (if configured)
        if (kickCallback_)
            kickCallback_();

        // Broadcast temp / heater state over WebSocket for live UI updates
        if (wsTempUpdateCallback_) wsTempUpdateCallback_();

        vTaskDelay(pdMS_TO_TICKS(config_.heaterTaskDelayS() * 1000));
    }
}

// ---------------- helpers ----------------

bool HeaterTask::turnHeaterOn(bool force)
{
    if (!isInDeadzone() || !dzEnabled_ || force)
    {
        isHeaterOn_ = true;
        return shelly_.switchOn();
    }
    return false;
}
bool HeaterTask::turnHeaterOff()
{
    isHeaterOn_ = false;
    return shelly_.switchOff();
}

bool HeaterTask::isInDeadzone() const
{
    if (!timekeeper::isValid())
    {
        return false; // or treat "no time" differently if you prefer
    }

    int m = timekeeper::localMinutesOfDay();
    if (m < 0)
        return false;

    uint16_t startMin = config_.deadzoneStartMin(); // [0..1439]
    uint16_t endMin = config_.deadzoneEndMin();     // [0..1439]

    // Normal range, e.g. 08:00–17:00
    if (startMin <= endMin)
    {
        return m >= startMin && m < endMin;
    }

    // Wrap-around range, e.g. 20:00–06:00
    return (m >= startMin) || (m < endMin);
}

String HeaterTask::logHeaterChange(bool isOn, float currentTemp) const
{
    String line;
    line.reserve(60);
    line += timekeeper::formatLocal(); // "YYYY-MM-DD HH:MM:SS"
    line += isOn ? " Heater turned ON" : " Heater turned OFF";
    line += " | Current: ";
    line += String(currentTemp, 1);
    line += "°C Target: ";
    line += String(config_.targetTemp(), 1);
    line += "°C";
    return line;
}

String HeaterTask::logDZChange(bool inDZ) const
{
    String line;
    line.reserve(60);
    line += timekeeper::formatLocal();
    line += inDZ ? " Entered deadzone" : " Exited deadzone";
    return line;
}
