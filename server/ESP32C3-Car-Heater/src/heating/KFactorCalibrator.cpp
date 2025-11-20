#include "heating/KFactorCalibrator.h"

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
