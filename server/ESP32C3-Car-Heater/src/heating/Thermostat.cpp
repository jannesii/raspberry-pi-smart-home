#include <Arduino.h>
#include "heating/Thermostat.h"

Thermostat::Thermostat(float targetTemp, float hysteresis)
    : targetTemp_(targetTemp),
      hysteresis_(hysteresis),
      heaterOn_(false),
      initialized_(false) {
      }

bool Thermostat::update(float currentTemp) {
    const float halfBand = hysteresis_ * 0.5f;

    if (!initialized_) {
        heaterOn_ = currentTemp < targetTemp_;
        initialized_ = true;
        Serial.printf("[Thermostat] Initial state: shouldHeat=%s (currentTemp=%.2f, targetTemp=%.2f)\n",
                      heaterOn_ ? "true" : "false",
                      currentTemp,
                      targetTemp_);
        return heaterOn_;
    }

    if (heaterOn_) {
        if (currentTemp >= targetTemp_ + halfBand) {
            heaterOn_ = false;
        }
    } else {
        if (currentTemp <= targetTemp_ - halfBand) {
            heaterOn_ = true;
        }
    }
    /* Serial.printf("[Thermostat] update: currentTemp=%.2f, targetTemp=%.2f, hysteresis=%.2f => shouldHeat=%s\n",
                  currentTemp,
                  targetTemp_,
                  hysteresis_,
                  heaterOn_ ? "true" : "false"); */

    return heaterOn_;
}

void Thermostat::setTarget(float targetTemp) { targetTemp_ = targetTemp; }
void Thermostat::setHysteresis(float hysteresis) { hysteresis_ = hysteresis; }

float Thermostat::target() const { return targetTemp_; }
float Thermostat::hysteresis() const { return hysteresis_; }
bool Thermostat::isHeaterOn() const { return heaterOn_; }
