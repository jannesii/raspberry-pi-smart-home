#include "heating/KFactorCalibrator.h"
#include <math.h>
#include <algorithm>

KFactorCalibrator::KFactorCalibrator()
    : calculator_() {}

float KFactorCalibrator::idealSecondsPerDegree() const
{
    const float massAir_kg = calculator_.airDensity() * calculator_.cabinVolume();
    const float energyPerDeg_J = massAir_kg * calculator_.specificHeat();
    const float idealSecondsPerDeg = energyPerDeg_J / calculator_.heaterPower();
    return idealSecondsPerDeg;
}

float KFactorCalibrator::deriveKFactor(float ambientTempC, float targetTempC, float observedWarmupSeconds) const
{
    const float deltaT = targetTempC - ambientTempC;
    if (deltaT <= 0.0f || observedWarmupSeconds <= 0.0f)
    {
        return -1.0f;
    }

    const float idealSecondsPerDeg = idealSecondsPerDegree();
    if (idealSecondsPerDeg <= 0.0f)
    {
        return -1.0f;
    }

    const float observedSecondsPerDeg = observedWarmupSeconds / deltaT;
    float k = observedSecondsPerDeg / idealSecondsPerDeg;

    // Keep k within a sane range to avoid accidental extreme inputs
    if (k < 0.1f)
        k = 0.1f;
    if (k > 500.0f)
        k = 500.0f;

    return k;
}

// -------- Calibration manager --------

#include "core/TimeKeeper.h"
#include "io/measurements.h"

namespace
{
constexpr const char *CALIB_NS = "kcal";
constexpr const char *CALIB_REC_KEY = "records";
constexpr const char *CALIB_COUNT_KEY = "count";
constexpr uint32_t MAX_RUN_SECONDS = 3 * 3600; // 3 hours ceiling
constexpr uint8_t BAND_WIDTH_C = 5;           // 5°C bands
constexpr uint8_t MAX_BAND = 30;              // up to ~150°C

const char *stateName(KFactorCalibrationManager::State s)
{
    switch (s)
    {
    case KFactorCalibrationManager::State::Idle:
        return "idle";
    case KFactorCalibrationManager::State::Scheduled:
        return "scheduled";
    case KFactorCalibrationManager::State::Running:
        return "running";
    default:
        return "unknown";
    }
}
} // namespace

KFactorCalibrationManager::KFactorCalibrationManager(Config &config,
                                                     HeaterTask &heaterTask,
                                                     ReadyByTask &readyByTask,
                                                     LogManager &logManager)
    : config_(config),
      heaterTask_(heaterTask),
      readyByTask_(readyByTask),
      logManager_(logManager)
{
}

void KFactorCalibrationManager::begin(uint32_t stackSize, UBaseType_t priority)
{
    prefs_.begin(CALIB_NS, false);
    loadRecords();
    if (task_ == nullptr)
    {
        xTaskCreate(&KFactorCalibrationManager::taskEntry,
                    "KCalib",
                    stackSize,
                    this,
                    priority,
                    &task_);
    }
}

bool KFactorCalibrationManager::schedule(float targetTempC, uint64_t startEpochUtc, String &err)
{
    if (!timekeeper::isTrulyValid())
    {
        err = "Time not synchronized";
        return false;
    }
    if (state_ != State::Idle)
    {
        err = "Calibration already in progress";
        return false;
    }

    targetTempC_ = targetTempC;
    uint64_t now = timekeeper::nowUtc();
    if (startEpochUtc == 0 || startEpochUtc <= now)
    {
        scheduledStartUtc_ = now;
    }
    else
    {
        scheduledStartUtc_ = startEpochUtc;
    }

    state_ = (scheduledStartUtc_ > now) ? State::Scheduled : State::Running;
    if (state_ == State::Running)
    {
        startRun();
    }
    notify();
    return true;
}

bool KFactorCalibrationManager::cancel()
{
    if (state_ == State::Idle)
        return false;

    heaterTask_.turnHeaterOff();
    restoreControl();
    state_ = State::Idle;
    notify();
    return true;
}

KFactorCalibrationManager::Status KFactorCalibrationManager::status() const
{
    Status s{};
    s.state = state_;
    s.targetTempC = targetTempC_;
    s.startEpochUtc = scheduledStartUtc_;
    s.ambientStartC = ambientStartC_;
    s.currentTempC = takeMeasurement(false).temperature;
    s.elapsedSeconds = (state_ == State::Running) ? (millis() - runStartMs_) / 1000 : 0;
    s.suggestedK = calibrator_.deriveKFactor(ambientStartC_, targetTempC_, s.elapsedSeconds);
    s.recordCount = recordCount_;
    s.records = records_;
    return s;
}

float KFactorCalibrationManager::derivedKFor(float ambientC, float targetC) const
{
    // Fall back to config value if no records
    if (recordCount_ == 0)
        return config_.kFactor();

    uint8_t band = bandForAmbient(ambientC);
    // Simple strategy: take newest record in same band; otherwise nearest band with data.
    int bestIdx = -1;
    uint8_t bestBand = 255;
    for (size_t i = 0; i < recordCount_; ++i)
    {
        uint8_t b = records_[i].band;
        if (bestIdx == -1 || (b == band) || (abs(int(b) - int(band)) < abs(int(bestBand) - int(band))))
        {
            bestIdx = static_cast<int>(i);
            bestBand = b;
            if (b == band)
                break;
        }
    }
    if (bestIdx >= 0)
        return records_[bestIdx].kFactor;
    return config_.kFactor();
}

void KFactorCalibrationManager::taskEntry(void *pvParameters)
{
    auto *self = static_cast<KFactorCalibrationManager *>(pvParameters);
    self->run();
}

void KFactorCalibrationManager::run()
{
    for (;;)
    {
        if (state_ == State::Idle)
        {
            maybeAutoCalibrate();
        }
        if (state_ == State::Scheduled)
        {
            if (timekeeper::isTrulyValid() && timekeeper::nowUtc() >= scheduledStartUtc_)
            {
                state_ = State::Running;
                startRun();
            }
        }
        else if (state_ == State::Running)
        {
            tickRun();
        }

        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

void KFactorCalibrationManager::startRun()
{
    if (!timekeeper::isTrulyValid())
    {
        state_ = State::Idle;
        notify();
        return;
    }

    readyByTask_.cancel();
    prevHeaterEnabled_ = heaterTask_.isEnabled();
    heaterTask_.setEnabled(false); // disable automation

    ambientStartC_ = takeMeasurement(false).temperature;
    runStartMs_ = millis();
    runStartEpochUtc_ = timekeeper::nowUtc();

    heaterTask_.turnHeaterOn(true);
    notify();

    char buf[128];
    snprintf(buf, sizeof(buf), "Starting kFactor calibration to %.1f°C (ambient %.1f°C)", targetTempC_, ambientStartC_);
    logManager_.append(buf);
}

void KFactorCalibrationManager::tickRun()
{
    float current = takeMeasurement(false).temperature;
    if (!heaterTask_.isHeaterOn())
    {
        heaterTask_.turnHeaterOn(true);
    }

    uint32_t elapsed = (millis() - runStartMs_) / 1000;
    if (current >= targetTempC_ || elapsed >= MAX_RUN_SECONDS)
    {
        float warmup = elapsed;
        float k = calibrator_.deriveKFactor(ambientStartC_, targetTempC_, warmup);
        finishRun(true, k, warmup);
        return;
    }

    if (elapsed % 5 == 0)
    {
        notify();
    }
}

void KFactorCalibrationManager::finishRun(bool success, float measuredK, float warmupSeconds)
{
    heaterTask_.turnHeaterOff();
    restoreControl();

    if (success && measuredK > 0.0f && isfinite(measuredK))
    {
        Record rec{ambientStartC_, targetTempC_, warmupSeconds, measuredK, runStartEpochUtc_, bandForAmbient(ambientStartC_)};
        saveRecord(rec);
        config_.setKFactor(measuredK);
        config_.save();
    }

    state_ = State::Idle;
    notify();

    char buf[128];
    snprintf(buf, sizeof(buf), "Calibration finished: k=%.2f, warmup=%.0fs", measuredK, warmupSeconds);
    logManager_.append(buf);
}

void KFactorCalibrationManager::restoreControl()
{
    heaterTask_.setEnabled(prevHeaterEnabled_);
}

void KFactorCalibrationManager::notify()
{
    if (updateCb_)
    {
        updateCb_();
    }
}

void KFactorCalibrationManager::loadRecords()
{
    recordCount_ = prefs_.getUChar(CALIB_COUNT_KEY, 0);
    if (recordCount_ > MAX_RECORDS)
        recordCount_ = 0;

    size_t n = prefs_.getBytes(CALIB_REC_KEY, records_.data(), sizeof(Record) * MAX_RECORDS);
    if (n != sizeof(Record) * MAX_RECORDS)
    {
        records_.fill(Record{0, 0, 0, 0, 0, 0});
        recordCount_ = 0;
    }
}

void KFactorCalibrationManager::saveRecord(const Record &rec)
{
    // Uniqueness rules: keep up to 2 per band; replace oldest similar target in band
    int similarIdx = similarIndex(rec.band, rec.targetC);
    if (similarIdx >= 0)
    {
        records_[similarIdx] = rec;
    }
    else
    {
        // If band full, replace oldest in that band; else insert at front
        int oldIdx = oldestIndexForBand(rec.band);
        if (oldIdx >= 0)
        {
            records_[oldIdx] = rec;
        }
        else
        {
            // shift down
            for (size_t i = MAX_RECORDS - 1; i > 0; --i)
            {
                records_[i] = records_[i - 1];
            }
            records_[0] = rec;
            if (recordCount_ < MAX_RECORDS)
                ++recordCount_;
        }
    }

    prefs_.putBytes(CALIB_REC_KEY, records_.data(), sizeof(Record) * MAX_RECORDS);
    prefs_.putUChar(CALIB_COUNT_KEY, static_cast<uint8_t>(recordCount_));
}

uint8_t KFactorCalibrationManager::bandForAmbient(float ambient) const
{
    if (!isfinite(ambient))
        return 0;
    int b = static_cast<int>(ambient / BAND_WIDTH_C);
    if (b < 0)
        b = 0;
    if (b > MAX_BAND)
        b = MAX_BAND;
    return static_cast<uint8_t>(b);
}

bool KFactorCalibrationManager::hasRecordForBand(uint8_t band) const
{
    for (size_t i = 0; i < recordCount_; ++i)
    {
        if (records_[i].band == band)
            return true;
    }
    return false;
}

int KFactorCalibrationManager::oldestIndexForBand(uint8_t band) const
{
    int idx = -1;
    uint64_t oldest = UINT64_MAX;
    size_t count = 0;
    for (size_t i = 0; i < recordCount_; ++i)
    {
        if (records_[i].band == band)
        {
            ++count;
            if (records_[i].epochUtc < oldest)
            {
                oldest = records_[i].epochUtc;
                idx = static_cast<int>(i);
            }
        }
    }
    // only allow 2 per band; if fewer than 2, signal "space available" with -1
    if (count < 2)
        return -1;
    return idx;
}

int KFactorCalibrationManager::similarIndex(uint8_t band, float targetC) const
{
    for (size_t i = 0; i < recordCount_; ++i)
    {
        if (records_[i].band == band && fabs(records_[i].targetC - targetC) < 3.0f)
            return static_cast<int>(i);
    }
    return -1;
}

bool KFactorCalibrationManager::inAutoWindow() const
{
    if (!timekeeper::isTrulyValid())
        return false;
    int m = timekeeper::localMinutesOfDay();
    if (m < 0)
        return false;
    uint16_t start = config_.autoCalibStartMin();
    uint16_t end = config_.autoCalibEndMin();
    if (start <= end)
        return m >= start && m < end;
    return (m >= start) || (m < end);
}

void KFactorCalibrationManager::maybeAutoCalibrate()
{
    if (!config_.autoCalibrationEnabled())
        return;
    if (!inAutoWindow())
        return;
    if (!timekeeper::isTrulyValid())
        return;

    // Avoid running if in the 2h window before a ReadyBy target, or heater already heating
    bool readyActive = config_.readyByActive();
    uint64_t rbEpoch = 0;
    float rbTemp = 0.0f;
    if (readyByTask_.getSchedule(rbEpoch, rbTemp))
        readyActive = true;

    if (heaterTask_.isHeaterOn())
        return;

    if (readyActive)
    {
        uint64_t now = timekeeper::nowUtc();
        if (now == 0 || rbEpoch == 0)
            return;
        uint64_t secondsLeft = (rbEpoch > now) ? (rbEpoch - now) : 0;
        if (secondsLeft <= 2 * 3600UL)
            return; // do not start within 2h of ReadyBy target
    }

    float ambient = takeMeasurement(false).temperature;
    float target = std::min(config_.targetTemp(), config_.autoCalibTargetCapC()); // guard against runaway targets
    float deltaT = target - ambient;
    if (!isfinite(ambient) || deltaT < 3.0f)
        return;

    uint8_t band = bandForAmbient(ambient);
    if (hasRecordForBand(band))
        return;

    // Schedule immediate run
    String err;
    schedule(target, 0, err);
}
