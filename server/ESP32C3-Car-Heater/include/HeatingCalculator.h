#pragma once

#include <Arduino.h>

// Simple physics-based warmup time estimator for the car cabin
class HeatingCalculator
{
public:
    HeatingCalculator(float cabinVolume_m3  = 2.8f,
                      float heaterPower_W   = 1000.0f,
                      float airDensity_kg_m3 = 1.2f,
                      float specificHeat_J_kgK = 1000.0f);

    // Estimate warmup time in seconds for going from ambientTempC -> targetTempC.
    // If targetTempC <= ambientTempC, returns 0.
    float estimateWarmupSeconds(float kFactor, float ambientTempC, float targetTempC) const;

    // Convenience: same as above but in minutes.
    float estimateWarmupMinutes(float kFactor, float ambientTempC, float targetTempC) const;

    // Accessors in case you want to show them in UI or adjust later
    float cabinVolume()    const { return cabinVolume_m3_; }
    float heaterPower()    const { return heaterPower_W_; }
    float airDensity()     const { return airDensity_kg_m3_; }
    float specificHeat()   const { return specificHeat_J_kgK_; }

private:
    float cabinVolume_m3_;
    float heaterPower_W_;
    float airDensity_kg_m3_;
    float specificHeat_J_kgK_;
};
