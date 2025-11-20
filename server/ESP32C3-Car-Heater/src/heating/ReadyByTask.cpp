#include "heating/ReadyByTask.h"

#include "io/measurements.h"
#include "core/TimeKeeper.h"
#include "heating/HeatingCalculator.h"

ReadyByTask::ReadyByTask(Config &config,
                         HeaterTask &heaterTask,
                         LogManager &logManager,
                         Thermostat &thermostat)
    : config_(config),
      heaterTask_(heaterTask),
      logManager_(logManager),
      thermostat_(thermostat)
{
}

void ReadyByTask::start(uint32_t stackSize, UBaseType_t priority)
{
    if (handle_ != nullptr)
    {
        Serial.println("[ReadyBy] Warning: ReadyBy task already running");
        log("Warning: ReadyBy task already running");
        return;
    }

    xTaskCreate(
        &ReadyByTask::taskEntry,
        "ReadyByTask",
        stackSize,
        this,
        priority,
        &handle_);

    Serial.println("[ReadyBy] Task started");
    log("Task started");
}

void ReadyByTask::taskEntry(void *pvParameters)
{
    auto *self = static_cast<ReadyByTask *>(pvParameters);
    self->run();
}

void ReadyByTask::schedule(uint64_t targetEpochUtc, float targetTempC)
{
    // Set fields first, then flip active_ true last.
    config_.setReadyByTargetEpochUtc(targetEpochUtc);
    config_.setReadyByTargetTemp(targetTempC);
    config_.setReadyByActive(true);
    heatingForced_ = false;
    targetTempReached_ = false;

    thermostat_.setTarget(targetTempC);
    thermostat_.setHysteresis(0.0f); // disable hysteresis during ReadyBy
    heaterTask_.setEnabled(false);

    // no need to save, "heaterTask_.setEnabled(false);" handles that
    // config_.save();

    String targetFormatted = timekeeper::formatEpoch(targetEpochUtc);
    Serial.printf("[ReadyBy] Scheduled: target time=%s, targetTemp=%.1f°C\n",
                  targetFormatted.c_str(), targetTempC);
    char buf[128];
    snprintf(
        buf,
        sizeof(buf),
        "Scheduled: target time=%s, targetTemp=%.1f°C",
        targetFormatted.c_str(), targetTempC);
    log(String(buf));
}

bool ReadyByTask::getSchedule(uint64_t &targetEpochUtc, float &targetTempC) const
{
    if (!config_.readyByActive())
    {
        return false;
    }
    targetEpochUtc = config_.readyByTargetEpochUtc();
    targetTempC = config_.readyByTargetTemp();
    return true;
}

void ReadyByTask::cancel()
{
    exitActions();
    log("Schedule cancelled by user.");
    Serial.println("[ReadyBy] Schedule cancelled by user.");
}

void ReadyByTask::run()
{
    log("Task started!");
    HeatingCalculator calculator;
    for (;;)
    {
        // If time isn't valid, we can't schedule properly
        if (!timekeeper::isValid())
        {
            vTaskDelay(pdMS_TO_TICKS(5000));
            continue;
        }

        if (!config_.readyByActive())
        {
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        uint64_t now = timekeeper::nowUtc();
        if (now == 0)
        {
            vTaskDelay(pdMS_TO_TICKS(2000));
            continue;
        }

        bool exiting = false;

        // Capture current schedule state into locals (avoid re-reading volatile)
        uint64_t targetUtc = config_.readyByTargetEpochUtc();
        float targetTmp = config_.readyByTargetTemp();

        // Measure current ambient
        float ambient = takeMeasurement(false).temperature;

        // If we somehow ended up past target time, stop forcing and clear schedule
        if (now >= targetUtc)
        {
            exiting = true;
            char buf[128];
            snprintf(
                buf,
                sizeof(buf),
                "Past target time, exiting. Reached temperature: %.1f/%.1f°C",
                ambient, targetTmp);
            log(String(buf));
            Serial.println("[ReadyBy] Schedule completed");
        }

        // Estimate how long we need to heat for current conditions
        float warmupSec = calculator.estimateWarmupSeconds(config_.kFactor(), ambient, targetTmp);
        if (warmupSec < 0.0f)
        {
            warmupSec = 0.0f;
        }

        uint64_t warmup = static_cast<uint64_t>(warmupSec);

        // Compute when we should start heating.
        // If warmup is larger than the time left, we start immediately.
        uint64_t startUtc;
        uint64_t secondsUntilTarget = (targetUtc > now) ? (targetUtc - now) : 0;

        if (warmup >= secondsUntilTarget)
        {
            startUtc = now; // start as soon as we can
        }
        else
        {
            startUtc = targetUtc - warmup;
        }

        // Start forcing heat if it's time and we haven't already done so
        if (now >= startUtc)
        {
            if (!heatingForced_)
            {
                heaterTask_.setEnabled(false); // disable normal thermostat control
                bool ok = heaterTask_.turnHeaterOn(true);
                Serial.printf("[ReadyBy] Forcing heater ON to meet schedule (ambient=%.1f°C, target=%.1f°C, warmup=%.0fs) -> %s\n",
                              ambient, targetTmp, warmupSec, ok ? "OK" : "FAILED");
                char buf[128];
                snprintf(
                    buf,
                    sizeof(buf),
                    "Forcing heater ON (ambient=%.1f°C, target=%.1f°C, warmup=%.0fs)",
                    ambient, targetTmp, warmupSec);
                log(String(buf));
                if (ok)
                {
                    heatingForced_ = true;
                }
            }
            bool shouldHeat = thermostat_.update(ambient);
            bool heaterOn = heaterTask_.isHeaterOn();
            if (!shouldHeat && !targetTempReached_)
            {
                // Target temp reached early
                char buf[128];
                snprintf(
                    buf,
                    sizeof(buf),
                    "Target temperature %.1f°C reached %.0f minutes early (ambient=%.1f°C)",
                    targetTmp, (static_cast<float>(secondsUntilTarget) / 60.0f), ambient);
                Serial.println("[ReadyBy] Target temperature reached; maintaining.");
                targetTempReached_ = true;
                thermostat_.setHysteresis(config_.hysteresis());
            }
            else if (shouldHeat && !heaterOn)
            {
                // Heater should be on but isn't
                heaterTask_.turnHeaterOn(true);
                Serial.println("[ReadyBy] Heater turned ON to maintain target temperature.");
                log("Heater turned ON to maintain target temperature.");
            }
            else if (!shouldHeat && heaterOn)
            {
                // Heater should be off but is on
                heaterTask_.turnHeaterOff();
                Serial.println("[ReadyBy] Heater turned OFF to maintain target temperature.");
                log("Heater turned OFF to maintain target temperature.");
            }
        }

        if (wsReadyByUpdateCallback_)
            wsReadyByUpdateCallback_();
        if (exiting)
            exitActions();

        // Re-evaluate roughly every 30 seconds; this lets us adjust if ambient changes.
        vTaskDelay(pdMS_TO_TICKS(30000));
    }
}

String ReadyByTask::log(const String &msg) const
{
    String line;
    line.reserve(60 + msg.length());
    line += timekeeper::formatLocal();
    line += " [ReadyByTask] ";
    line += msg;
    logManager_.append(line);
    return line;
}

void ReadyByTask::exitActions()
{
    config_.setReadyByActive(false);
    heatingForced_ = false;
    targetTempReached_ = false;
    thermostat_.setTarget(config_.targetTemp());
    thermostat_.setHysteresis(config_.hysteresis());
    heaterTask_.setEnabled(true); // re-enable normal thermostat control

    config_.save();
}