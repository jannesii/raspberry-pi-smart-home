#include "heating/HeatingCalculator.h"

HeatingCalculator::HeatingCalculator(float cabinVolume_m3,
                                     float heaterPower_W,
                                     float airDensity_kg_m3,
                                     float specificHeat_J_kgK)
    : cabinVolume_m3_(cabinVolume_m3),
      heaterPower_W_(heaterPower_W),
      airDensity_kg_m3_(airDensity_kg_m3),
      specificHeat_J_kgK_(specificHeat_J_kgK)
{
}

float HeatingCalculator::estimateWarmupSeconds(float kFactor, float ambientTempC, float targetTempC) const
{
    float deltaT = targetTempC - ambientTempC;
    if (deltaT <= 0.0f) {
        return 0.0f;
    }

    // Mass of cabin air
    const float massAir_kg = airDensity_kg_m3_ * cabinVolume_m3_; // kg

    // Energy required per °C
    const float energyPerDeg_J = massAir_kg * specificHeat_J_kgK_; // J/°C

    // Ideal time per °C (no losses), seconds per degree
    const float idealSecondsPerDeg = energyPerDeg_J / heaterPower_W_; // s/°C

    // Real-world time per °C with kFactor scaling
    const float effectiveSecondsPerDeg = idealSecondsPerDeg * kFactor;

    const float totalSeconds = effectiveSecondsPerDeg * deltaT;

    // Clamp to a reasonable range, e.g. 0..4 hours (optional)
    const float maxSeconds = 4.0f * 3600.0f;
    if (totalSeconds < 0.0f) return 0.0f;
    if (totalSeconds > maxSeconds) return maxSeconds;

    return totalSeconds;
}

float HeatingCalculator::estimateWarmupMinutes(float kFactor, float ambientTempC, float targetTempC) const
{
    return estimateWarmupSeconds(kFactor, ambientTempC, targetTempC) / 60.0f;
}
