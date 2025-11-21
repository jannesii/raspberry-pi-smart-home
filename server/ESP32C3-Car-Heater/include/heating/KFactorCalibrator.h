#pragma once

#include <Arduino.h>
#include "heating/HeatingCalculator.h"
#include "core/Config.h"
#include "heating/HeaterTask.h"
#include "heating/ReadyByTask.h"
#include "core/LogManager.h"
#include <Preferences.h>
#include <functional>
#include <array>

// Utility to derive a kFactor based on an observed warmup time.
// kFactor scales the ideal physics estimate to match the real world.
class KFactorCalibrator
{
public:
  KFactorCalibrator();

  // Given ambient, target, and observed warmup time (seconds), compute kFactor.
  // Returns -1.0f on invalid input (non-positive deltaT or warmup time).
  float deriveKFactor(float ambientTempC, float targetTempC, float observedWarmupSeconds) const;

  // Baseline seconds needed to raise 1°C in a perfectly insulated cabin (k=1).
  float idealSecondsPerDegree() const;

  // Surface the physics constants for UI/debugging
  float cabinVolume() const { return calculator_.cabinVolume(); }
  float heaterPower() const { return calculator_.heaterPower(); }
  float airDensity() const { return calculator_.airDensity(); }
  float specificHeat() const { return calculator_.specificHeat(); }

private:
  HeatingCalculator calculator_;
};

// Manages calibration runs (scheduled or immediate), keeps history in NVS,
// and owns the exclusive heating phase used for calibration.
class KFactorCalibrationManager
{
public:
  struct Record
  {
    float ambientC;
    float targetC;
    float warmupSeconds;
    float kFactor;
    uint64_t epochUtc;
    uint8_t band;
  };

  enum class State
  {
    Idle,
    Scheduled,
    Running
  };

  struct Status
  {
    State state;
    float targetTempC;
    uint64_t startEpochUtc;
    float ambientStartC;
    float currentTempC;
    uint32_t elapsedSeconds;
    float suggestedK;
    size_t recordCount;
    std::array<Record, 12> records;
  };

  using UpdateCallback = std::function<void(void)>;

  KFactorCalibrationManager(Config &config,
                            HeaterTask &heaterTask,
                            ReadyByTask &readyByTask,
                            LogManager &logManager);

  void begin(uint32_t stackSize = 4096, UBaseType_t priority = 1);

  // Schedule calibration. startEpochUtc=0 means immediate.
  bool schedule(float targetTempC, uint64_t startEpochUtc, String &err);
  bool cancel();

  bool isBusy() const { return state_ != State::Idle; }
  bool isRunning() const { return state_ == State::Running; }
  bool isScheduled() const { return state_ == State::Scheduled; }

  Status status() const;
  float derivedKFor(float ambientC, float targetC) const;
  bool deleteRecord(uint64_t epochUtc);

  void setUpdateCallback(UpdateCallback cb) { updateCb_ = cb; }

private:
  // Internal helpers
  bool shouldLogAutoSkip();
  void logAutoSkip(const String &msg);

  static void taskEntry(void *pvParameters);
  void run();
  void startRun();
  void tickRun();
  void finishRun(bool success, float measuredK, float warmupSeconds);
  void restoreControl();
  void notify();

  void loadRecords();
  void saveRecord(const Record &rec);
  uint8_t bandForAmbient(float ambient) const;
  bool hasRecordForBand(uint8_t band) const;
  int oldestIndexForBand(uint8_t band) const;
  int similarIndex(uint8_t band, float targetC) const;
  float globalAverageK() const;
  bool inAutoWindow() const;
  void maybeAutoCalibrate();
  String log(const String &msg) const;

  Config &config_;
  HeaterTask &heaterTask_;
  ReadyByTask &readyByTask_;
  LogManager &logManager_;
  Preferences prefs_;
  KFactorCalibrator calibrator_;

  TaskHandle_t task_ = nullptr;
  volatile State state_ = State::Idle;

  float targetTempC_ = 0.0f;
  uint64_t scheduledStartUtc_ = 0;
  float ambientStartC_ = NAN;
  uint64_t runStartEpochUtc_ = 0;
  uint32_t runStartMs_ = 0;
  bool prevHeaterEnabled_ = true;
  bool prevReadyByActive_ = false;

  static constexpr size_t MAX_RECORDS = 12;
  std::array<Record, MAX_RECORDS> records_{};
  size_t recordCount_ = 0;

  // Track whether the current run was started by auto-calibration
  bool autoRequested_ = false;
  // Rate-limit auto-calibration skip logs so we don't spam the small log buffer
  static constexpr uint32_t AUTO_SKIP_LOG_INTERVAL_MS = 20UL * 60UL * 1000UL; // 20 minutes
  uint32_t lastAutoSkipLogMs_ = 0;

  UpdateCallback updateCb_{nullptr};
};
