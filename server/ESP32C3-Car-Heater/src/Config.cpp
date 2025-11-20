// Config.cpp
#include "Config.h"

namespace {
constexpr const char* NAMESPACE = "config";
}

// Define the field table (class static member)
const Config::FloatFieldDesc Config::FLOAT_FIELDS[] = {
    { "target_temp",    10.0f,      &Config::targetTemp_  },
    { "hysteresis",     3.0f,       &Config::hysteresis_  },
    { "heater_delay",   10.0f,      &Config::heaterTaskDelayS_ },
    { "dz_start_min",   20.f * 60,  &Config::deadzoneStartMinF_  }, // 20:00
    { "dz_end_min",     6.f * 60,   &Config::deadzoneEndMinF_    }, // 06:00
    { "k_factor",       20.99f,     &Config::kFactor_        },
    { "readyby_target_temp", 22.0f, &Config::readyByTargetTemp_ }
};

// Define boolean fields
const Config::BoolFieldDesc Config::BOOL_FIELDS[] = {
    { "dz_enabled",           true,  &Config::deadzoneEnabled_ },
    { "heater_task_enabled",  true,  &Config::heaterTaskEnabled_ },
    { "readyby_enabled",      false, &Config::readyByActive_ }
};

// Define uint64 fields
const Config::Uint64FieldDesc Config::UINT64_FIELDS[] = {
    { "readyby_target_epoch_utc", 0ULL, &Config::readyByTargetEpochUtc_ }
};

// helpers to persist uint64 via bytes (works across core versions)
static uint64_t prefsGetU64(Preferences &prefs, const char* key, uint64_t defVal) {
    if (!prefs.isKey(key)) return defVal;
    uint64_t val = 0ULL;
    size_t n = prefs.getBytes(key, &val, sizeof(val));
    if (n != sizeof(val)) return defVal;
    return val;
}

static void prefsPutU64(Preferences &prefs, const char* key, uint64_t val) {
    prefs.putBytes(key, &val, sizeof(val));
}

// No NUM_FLOAT_FIELDS needed

Config::Config()
    : dirty_(false)
{
    // init float fields from descriptor defaults
    for (const auto& f : FLOAT_FIELDS) {
        this->*(f.member) = f.defaultValue;
    }
    // init boolean fields from descriptor defaults
    for (const auto& b : BOOL_FIELDS) {
        this->*(b.member) = b.defaultValue;
    }
    // init uint64 fields from descriptor defaults
    for (const auto& u : UINT64_FIELDS) {
        this->*(u.member) = u.defaultValue;
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

    // Load boolean fields
    for (const auto& b : BOOL_FIELDS) {
        if (!prefs_.isKey(b.key)) {
            this->*(b.member) = b.defaultValue;
            anyMissing = true;
        } else {
            this->*(b.member) = prefs_.getBool(b.key, b.defaultValue);
            Serial.printf("[Config] Loaded key '%s' = %s\n",
                          b.key,
                          (this->*(b.member)) ? "true" : "false");
        }
    }

    // Load uint64 fields
    for (const auto& u : UINT64_FIELDS) {
        if (!prefs_.isKey(u.key)) {
            this->*(u.member) = u.defaultValue;
            anyMissing = true;
        } else {
            this->*(u.member) = prefsGetU64(prefs_, u.key, u.defaultValue);
            Serial.printf("[Config] Loaded key '%s' = %llu\n",
                          u.key,
                          (unsigned long long)(this->*(u.member)));
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

    for (const auto& b : BOOL_FIELDS) {
        prefs_.putBool(b.key, this->*(b.member));
        Serial.printf("[Config] Saved key '%s' = %s\n",
                      b.key,
                      (this->*(b.member)) ? "true" : "false");
    }

    for (const auto& u : UINT64_FIELDS) {
        prefsPutU64(prefs_, u.key, this->*(u.member));
        Serial.printf("[Config] Saved key '%s' = %llu\n",
                      u.key,
                      (unsigned long long)(this->*(u.member)));
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

void Config::setKFactor(float v) {
    if (v == kFactor_) return;
    kFactor_ = v;
    dirty_ = true;
}

void Config::setReadyByTargetTemp(float v) {
    if (v == readyByTargetTemp_) return;
    readyByTargetTemp_ = v;
    dirty_ = true;
}

void Config::setDeadzoneEnabled(bool v) {
    if (v == deadzoneEnabled_) return;
    deadzoneEnabled_ = v;
    dirty_ = true;
}

void Config::setHeaterTaskEnabled(bool v) {
    if (v == heaterTaskEnabled_) return;
    heaterTaskEnabled_ = v;
    dirty_ = true;
}

void Config::setReadyByActive(bool v) {
    if (v == readyByActive_) return;
    readyByActive_ = v;
    dirty_ = true;
}

void Config::setReadyByTargetEpochUtc(uint64_t v) {
    if (v == readyByTargetEpochUtc_) return;
    readyByTargetEpochUtc_ = v;
    dirty_ = true;
}
