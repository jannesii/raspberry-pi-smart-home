#pragma once

#include "heating/HeatingCalculator.h"

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
