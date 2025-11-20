#include "ReadyByTask.h"

#include "measurements.h"
#include "TimeKeeper.h"
#include "HeatingCalculator.h"

ReadyByTask::ReadyByTask(Config &config,
                         HeaterTask &heaterTask,
                         LogManager &logManager)
    : config_(config),
      heaterTask_(heaterTask),
      logManager_(logManager)
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
    targetEpochUtc_ = targetEpochUtc;
    targetTempC_    = targetTempC;
    heatingForced_  = false;
    active_         = true;

    String targetFormatted = timekeeper::formatEpoch(targetEpochUtc);
    Serial.printf("[ReadyBy] Scheduled: target time=%s, targetTemp=%.1f°C\n",
                  targetFormatted.c_str(), targetTempC);
    char buf[128];
    snprintf(
        buf,
        sizeof(buf),
        "Scheduled: target time=%s, targetTemp=%.1f°C",
        targetFormatted.c_str(), targetTempC
    );
    start();
    log(String(buf));
}

bool ReadyByTask::getSchedule(uint64_t &targetEpochUtc, float &targetTempC) const
{
    if (!active_) {
        return false;
    }
    targetEpochUtc = targetEpochUtc_;
    targetTempC    = targetTempC_;
    return true;
}

void ReadyByTask::cancel()
{
    // Mark as inactive first so task can see it and exit any plan
    active_ = false;

    // If we were currently forcing heat on, switch it off
    if (heatingForced_)
    {
        bool ok = heaterTask_.turnHeaterOff();
        Serial.printf("[ReadyBy] Cancel: turnHeaterOff() %s\n", ok ? "OK" : "FAILED");
        heatingForced_ = false;
    }
    stop();
    Serial.println("[ReadyBy] Schedule cancelled");
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

        if (!active_)
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

        // Capture current schedule state into locals (avoid re-reading volatile)
        uint64_t targetUtc = targetEpochUtc_;
        float    targetTmp = targetTempC_;

        // If we somehow ended up past target time, stop forcing and clear schedule
        if (now >= targetUtc)
        {
            if (heatingForced_)
            {
                heaterTask_.turnHeaterOff();
                log("Target reached; shutting down...");
                Serial.printf("[ReadyBy] Target reached; shutting down.");
                heatingForced_ = false;
            }
            heaterTask_.start(); // re-enable normal thermostat control
            active_ = false;
            log("Schedule completed");
            Serial.println("[ReadyBy] Schedule completed");
            vTaskDelay(pdMS_TO_TICKS(1000));
            this->stop();
            break;
        }

        // Measure current ambient
        float ambient = takeMeasurement(false).temperature;

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
        if (!heatingForced_ && now >= startUtc)
        {
            heaterTask_.stop(); // disable normal thermostat control
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
        if (wsReadyByUpdateCallback_) wsReadyByUpdateCallback_();
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