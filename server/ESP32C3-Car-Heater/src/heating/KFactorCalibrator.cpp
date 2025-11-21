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
    if (!isfinite(idealSecondsPerDeg) || idealSecondsPerDeg <= 0.0f)
        return -1.0f;


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
constexpr int AMBIENT_MIN_C   = -30;  // lowest temp we care about
constexpr int AMBIENT_MAX_C   =  20;  // highest temp we care about (car use, winter)
constexpr uint8_t BAND_WIDTH_C = 5;   // 5°C bands
constexpr float MIN_EFFECT_DELTA_C         = 1.0f;    // must heat at least 1°C
constexpr uint32_t NO_EFFECT_TIMEOUT_SEC   = 20 * 60; // after 20 min with <1°C change, abort
constexpr float MIN_AUTO_DELTA_C = 5.0f;


// Number of bands for -30..20 with width 5°C → 50/5 = 10 → bands 0..10
constexpr uint8_t MAX_BAND =
    (AMBIENT_MAX_C - AMBIENT_MIN_C) / BAND_WIDTH_C;

static_assert(MAX_BAND <= 255, "MAX_BAND must fit in uint8_t");



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

    if (state_ == State::Running)
    {
        s.elapsedSeconds = (millis() - runStartMs_) / 1000;
        const float deltaSoFar = s.currentTempC - ambientStartC_;
        if (deltaSoFar > 0.5f)  // arbitrary “enough progress” threshold
        {
            const float pseudoTarget = ambientStartC_ + deltaSoFar;
            s.suggestedK = calibrator_.deriveKFactor(
                ambientStartC_, pseudoTarget, s.elapsedSeconds);
        }
        else
        {
            s.suggestedK = -1.0f; // not enough data yet
        }
    }
    else
    {
        s.elapsedSeconds = 0;
        s.suggestedK = -1.0f;
    }

    s.recordCount = recordCount_;
    s.records = records_;
    return s;
}


float KFactorCalibrationManager::derivedKFor(float ambientC, float targetC) const
{
    // Fall back if no data
    if (recordCount_ == 0)
        return config_.kFactor();

    uint8_t band = bandForAmbient(ambientC);

    float sumK = 0.0f;
    float sumW = 0.0f;

    for (size_t i = 0; i < recordCount_; ++i)
    {
        const Record &r = records_[i];
        if (r.kFactor <= 0.0f || !isfinite(r.kFactor))
            continue;

        // Distance in ambient band
        float bandDist = fabs(static_cast<float>(r.band) - static_cast<float>(band));

        // Distance between this record's target and our requested target
        float targetDist = fabs(r.targetC - targetC);

        // Weight:
        //  - exact same band & target → weight ~1
        //  - further away in band or target → weight shrinks
        float w = 1.0f / (1.0f + bandDist + (targetDist / 5.0f)); // 5°C scale for target

        sumK += r.kFactor * w;
        sumW += w;
    }

    if (sumW > 0.0f)
        return sumK / sumW;

    // Fallback if everything was invalid
    return config_.kFactor();
}



float KFactorCalibrationManager::globalAverageK() const
{
    float sum = 0.0f;
    size_t count = 0;

    for (size_t i = 0; i < recordCount_; ++i)
    {
        const Record &r = records_[i];
        if (r.kFactor > 0.0f && isfinite(r.kFactor))
        {
            sum += r.kFactor;
            ++count;
        }
    }

    if (count == 0)
        return config_.kFactor(); // or some default

    return sum / static_cast<float>(count);
}



void KFactorCalibrationManager::taskEntry(void *pvParameters)
{
    auto *self = static_cast<KFactorCalibrationManager *>(pvParameters);
    self->run();
}

bool KFactorCalibrationManager::shouldLogAutoSkip()
{
    uint32_t now = millis();
    if (lastAutoSkipLogMs_ == 0 || (now - lastAutoSkipLogMs_) >= AUTO_SKIP_LOG_INTERVAL_MS)
    {
        lastAutoSkipLogMs_ = now;
        return true;
    }
    return false;
}

void KFactorCalibrationManager::logAutoSkip(const String &msg)
{
    if (!shouldLogAutoSkip())
        return;
    log(msg);
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
        autoRequested_ = false;
        notify();
        return;
    }

    prevReadyByActive_ = readyByTask_.isActive();
    readyByTask_.setActive(false); // disable ReadyBy during calibration
    prevHeaterEnabled_ = heaterTask_.isEnabled();
    heaterTask_.setEnabled(false); // disable automation

    ambientStartC_ = takeMeasurement(false).temperature;
    runStartMs_ = millis();
    runStartEpochUtc_ = timekeeper::nowUtc();

    heaterTask_.turnHeaterOn(true);
    notify();

    char buf[128];
    uint8_t band = bandForAmbient(ambientStartC_);
    const char *mode = autoRequested_ ? "auto" : "manual";
    snprintf(buf, sizeof(buf),
             "Starting %s kFactor calibration to %.1f°C (ambient %.1f°C, band %u)",
             mode,
             targetTempC_,
             ambientStartC_,
             static_cast<unsigned>(band));
    log(buf);
}

void KFactorCalibrationManager::tickRun()
{
    float current = takeMeasurement(false).temperature;
    if (!heaterTask_.isHeaterOn())
    {
        heaterTask_.turnHeaterOn(true);
    }

    uint32_t elapsed = (millis() - runStartMs_) / 1000;
    float deltaFromStart = current - ambientStartC_;

    // --- NEW: abort if there is clearly no heating effect ---
    if (elapsed >= NO_EFFECT_TIMEOUT_SEC && deltaFromStart < MIN_EFFECT_DELTA_C)
    {
        char buf[128];
        snprintf(buf, sizeof(buf),
                 "Calibration aborted: no heating effect detected (ΔT=%.1f°C after %lus)",
                 deltaFromStart, static_cast<unsigned long>(elapsed));
        log(buf);

        // Do NOT save any k for this run
        finishRun(false, -1.0f, static_cast<float>(elapsed));
        return;
    }
    // --- END NEW ---

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

    const bool wasAuto = autoRequested_;

    if (success && measuredK > 0.0f && isfinite(measuredK))
    {
        Record rec{ambientStartC_, targetTempC_, warmupSeconds, measuredK,
                   runStartEpochUtc_, bandForAmbient(ambientStartC_)};
        saveRecord(rec);

        // Use a smoothed global k from all records instead of just this one
        float globalK = globalAverageK();
        if (globalK > 0.0f && isfinite(globalK))
        {
            config_.setKFactor(globalK);
            config_.save();
        }
    }

    state_ = State::Idle;
    autoRequested_ = false;
    notify();

    char buf[128];
    uint8_t band = bandForAmbient(ambientStartC_);
    const char *mode = wasAuto ? "auto" : "manual";
    snprintf(buf, sizeof(buf),
             "%s calibration finished: k=%.2f, warmup=%.0fs (ambient %.1f°C → %.1f°C, band %u)",
             mode,
             measuredK,
             warmupSeconds,
             ambientStartC_,
             targetTempC_,
             static_cast<unsigned>(band));
    log(buf);
}


void KFactorCalibrationManager::restoreControl()
{
    readyByTask_.setActive(prevReadyByActive_);
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

bool KFactorCalibrationManager::deleteRecord(uint64_t epochUtc)
{
    if (recordCount_ == 0)
        return false;

    int idx = -1;
    for (size_t i = 0; i < recordCount_; ++i)
    {
        if (records_[i].epochUtc == epochUtc)
        {
            idx = static_cast<int>(i);
            break;
        }
    }
    if (idx < 0)
        return false;

    Record removed = records_[static_cast<size_t>(idx)];

    // Shift remaining records down to keep ordering
    for (size_t i = static_cast<size_t>(idx); i + 1 < recordCount_; ++i)
    {
        records_[i] = records_[i + 1];
    }
    // Clear trailing slot
    records_[recordCount_ - 1] = Record{0, 0, 0, 0, 0, 0};
    --recordCount_;

    prefs_.putBytes(CALIB_REC_KEY, records_.data(), sizeof(Record) * MAX_RECORDS);
    prefs_.putUChar(CALIB_COUNT_KEY, static_cast<uint8_t>(recordCount_));

    float globalK = globalAverageK();
    if (globalK > 0.0f && isfinite(globalK))
    {
        config_.setKFactor(globalK);
        config_.save();
    }

    char buf[128];
    snprintf(buf, sizeof(buf),
             "Deleted calibration record k=%.2f (%.1f°C → %.1f°C)",
             removed.kFactor, removed.ambientC, removed.targetC);
    log(buf);

    notify();
    return true;
}

uint8_t KFactorCalibrationManager::bandForAmbient(float ambient) const
{
    if (!isfinite(ambient))
        return 0;

    // Shift so AMBIENT_MIN_C (-30) maps to 0
    // e.g. ambient = -30 → shifted = 0
    //      ambient =   0 → shifted = 30
    float shifted = ambient - static_cast<float>(AMBIENT_MIN_C);

    int b = static_cast<int>(shifted / static_cast<float>(BAND_WIDTH_C));

    // Clamp to valid range
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
    if (!timekeeper::isTrulyValid())
        return;
    if (!inAutoWindow())
        return;

    // Avoid running if in the 2h window before a ReadyBy target, or heater already heating
    bool readyActive = config_.readyByActive();
    uint64_t rbEpoch = 0;
    float rbTemp = 0.0f;
    if (readyByTask_.getSchedule(rbEpoch, rbTemp))
        readyActive = true;

    if (heaterTask_.isHeaterOn())
    {
        logAutoSkip(F("Auto calibration skipped: heater already on"));
        return;
    }

    if (readyActive)
    {
        uint64_t now = timekeeper::nowUtc();
        if (now == 0 || rbEpoch == 0)
            return;
        uint64_t secondsLeft = (rbEpoch > now) ? (rbEpoch - now) : 0;
        if (secondsLeft <= 2 * 3600UL)
        {
            char buf[96];
            snprintf(buf,
                     sizeof(buf),
                     "Auto calibration skipped: ReadyBy target in %lu min",
                     static_cast<unsigned long>(secondsLeft / 60UL));
            logAutoSkip(buf);
            return; // do not start within 2h of ReadyBy target
        }
    }

    float ambient = takeMeasurement(false).temperature;
    float target = config_.autoCalibTargetCapC();
    float deltaT = target - ambient;
    if (!isfinite(ambient) || deltaT < MIN_AUTO_DELTA_C)
    {
        char buf[128];
        snprintf(buf,
                 sizeof(buf),
                 "Auto calibration skipped: insufficient deltaT (ambient=%.1f°C, target=%.1f°C)",
                 ambient,
                 target);
        logAutoSkip(buf);
        return;
    }

    uint8_t band = bandForAmbient(ambient);
    if (hasRecordForBand(band))
    {
        char buf[96];
        snprintf(buf,
                 sizeof(buf),
                 "Auto calibration skipped: band %u already has record",
                 static_cast<unsigned>(band));
        logAutoSkip(buf);
        return;
    }

    // Schedule immediate run
    String err;
    autoRequested_ = true;
    if (!schedule(target, 0, err))
    {
        char buf[128];
        snprintf(buf,
                 sizeof(buf),
                 "Auto calibration failed to schedule: %s",
                 err.c_str());
        logAutoSkip(buf);
        autoRequested_ = false;
    }
    else
    {
        char buf[128];
        snprintf(buf,
                 sizeof(buf),
                 "Auto calibration scheduled to %.1f°C (ambient %.1f°C, band %u)",
                 target,
                 ambient,
                 static_cast<unsigned>(band));
        log(buf);
    }
}
String KFactorCalibrationManager::log(const String &msg) const
{
    String line;
    line.reserve(60 + msg.length());
    line += timekeeper::formatLocal();
    line += " [CalibMgr] ";
    line += msg;
    logManager_.append(line);
    return line;
}
