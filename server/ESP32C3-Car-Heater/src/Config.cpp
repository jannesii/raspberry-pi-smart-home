// Config.cpp
#include "Config.h"

namespace {
constexpr const char* NAMESPACE = "config";
}

// Define the field table (class static member)
const Config::FloatFieldDesc Config::FLOAT_FIELDS[] = {
    { "target_temp",    10.0f,       &Config::targetTemp_  },
    { "hysteresis",     3.0f,       &Config::hysteresis_  },
    { "heater_delay",   10.0f,      &Config::heaterTaskDelayS_ },
    { "dz_start_min",   20.f * 60,  &Config::deadzoneStartMinF_  }, // 20:00
    { "dz_end_min",     6.f * 60,   &Config::deadzoneEndMinF_    }, // 06:00
    { "k_factor",       20.99f,     &Config::kFactor_        }
};

// No NUM_FLOAT_FIELDS needed

Config::Config()
    : dirty_(false)
{
    // init fields from descriptor defaults
    for (const auto& f : FLOAT_FIELDS) {
        this->*(f.member) = f.defaultValue;
    }
}

bool Config::begin() {
    if (!prefs_.begin(NAMESPACE, /*readOnly*/ false)) {
        return false;
    }
    load();
    return true;
}

void Config::load() {
    bool anyMissing = false;

    for (const auto& f : FLOAT_FIELDS) {
        if (!prefs_.isKey(f.key)) {
            // Key missing → use default
            this->*(f.member) = f.defaultValue;
            anyMissing = true;
        } else {
            this->*(f.member) = prefs_.getFloat(f.key, f.defaultValue);
            Serial.printf("[Config] Loaded key '%s' = %.2f\n",
                          f.key,
                          this->*(f.member));
        }
    }

    dirty_ = anyMissing;
    if (anyMissing) {
        save();   // persist defaults for missing keys
    }
}

void Config::save() const {
    if (!dirty_) return;

    for (const auto& f : FLOAT_FIELDS) {
        prefs_.putFloat(f.key, this->*(f.member));
        Serial.printf("[Config] Saved key '%s' = %.2f\n",
                      f.key,
                      this->*(f.member));
    }

    dirty_ = false;
}

void Config::setTargetTemp(float v) {
    if (v == targetTemp_) return;
    targetTemp_ = v;
    dirty_ = true;
}

void Config::setHysteresis(float v) {
    if (v == hysteresis_) return;
    hysteresis_ = v;
    dirty_ = true;
}

void Config::setHeaterTaskDelayS(float v) {
    if (v == heaterTaskDelayS_) return;
    heaterTaskDelayS_ = v;
    dirty_ = true;
}

uint16_t Config::deadzoneStartMin() const {
    float v = deadzoneStartMinF_;
    if (v < 0.f) v = 0.f;
    if (v > 1439.f) v = 1439.f;
    return static_cast<uint16_t>(v + 0.5f); // round to nearest
}

uint16_t Config::deadzoneEndMin() const {
    float v = deadzoneEndMinF_;
    if (v < 0.f) v = 0.f;
    if (v > 1439.f) v = 1439.f;
    return static_cast<uint16_t>(v + 0.5f);
}

void Config::setDeadzoneStartMin(uint16_t m) {
    float v = static_cast<float>(m);
    if (v != deadzoneStartMinF_) {
        deadzoneStartMinF_ = v;
        dirty_ = true;
    }
}

void Config::setDeadzoneEndMin(uint16_t m) {
    float v = static_cast<float>(m);
    if (v != deadzoneEndMinF_) {
        deadzoneEndMinF_ = v;
        dirty_ = true;
    }
}
