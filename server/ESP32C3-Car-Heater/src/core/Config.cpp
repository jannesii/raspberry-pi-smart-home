// Config.cpp
#include "core/Config.h"

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
    { "rb_tt",          22.0f,      &Config::readyByTargetTemp_ },
    { "ac_smin",        2.f * 60,   &Config::autoCalibStartMinF_ }, // 02:00
    { "ac_emin",        5.f * 60,   &Config::autoCalibEndMinF_ },   // 05:00
    { "ac_cap",         20.0f,      &Config::autoCalibTargetCap_ }
};

// Define boolean fields
const Config::BoolFieldDesc Config::BOOL_FIELDS[] = {
    { "dz_enabled",           true,  &Config::deadzoneEnabled_ },
    { "ht_en",                true,  &Config::heaterTaskEnabled_ },
    { "rb_en",                false, &Config::readyByActive_ },
    { "ac_en",                false, &Config::autoCalibrationEnabled_ }
};

// Define uint64 fields
const Config::Uint64FieldDesc Config::UINT64_FIELDS[] = {
    { "rb_epoch", 0ULL, &Config::readyByTargetEpochUtc_ }
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

    auto legacyFloatKey = [](const char* key) -> const char* {
        if (strcmp(key, "rb_tt") == 0) return "readyby_target_temp";
        if (strcmp(key, "ac_smin") == 0) return "auto_calib_start_min";
        if (strcmp(key, "ac_emin") == 0) return "auto_calib_end_min";
        if (strcmp(key, "ac_cap") == 0) return "auto_calib_target_cap";
        return nullptr;
    };
    auto legacyBoolKey = [](const char* key) -> const char* {
        if (strcmp(key, "ht_en") == 0) return "heater_task_enabled";
        if (strcmp(key, "rb_en") == 0) return "readyby_enabled";
        if (strcmp(key, "ac_en") == 0) return "auto_calib_enabled";
        return nullptr;
    };
    auto legacyU64Key = [](const char* key) -> const char* {
        if (strcmp(key, "rb_epoch") == 0) return "readyby_target_epoch_utc";
        return nullptr;
    };

    for (const auto& f : FLOAT_FIELDS) {
        if (!prefs_.isKey(f.key)) {
            const char* legacy = legacyFloatKey(f.key);
            if (legacy && prefs_.isKey(legacy)) {
                this->*(f.member) = prefs_.getFloat(legacy, f.defaultValue);
                anyMissing = true; // migrate to new key
                Serial.printf("[Config] Migrated legacy float '%s' -> '%s' = %.2f\n", legacy, f.key, this->*(f.member));
            } else {
                // Key missing → use default
                this->*(f.member) = f.defaultValue;
                anyMissing = true;
            }
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
            const char* legacy = legacyBoolKey(b.key);
            if (legacy && prefs_.isKey(legacy)) {
                this->*(b.member) = prefs_.getBool(legacy, b.defaultValue);
                anyMissing = true;
                Serial.printf("[Config] Migrated legacy bool '%s' -> '%s' = %s\n",
                              legacy, b.key, (this->*(b.member)) ? "true" : "false");
            } else {
                this->*(b.member) = b.defaultValue;
                anyMissing = true;
            }
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
            const char* legacy = legacyU64Key(u.key);
            if (legacy && prefs_.isKey(legacy)) {
                this->*(u.member) = prefsGetU64(prefs_, legacy, u.defaultValue);
                anyMissing = true;
                Serial.printf("[Config] Migrated legacy u64 '%s' -> '%s' = %llu\n",
                              legacy, u.key, (unsigned long long)(this->*(u.member)));
            } else {
                this->*(u.member) = u.defaultValue;
                anyMissing = true;
            }
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

uint16_t Config::autoCalibStartMin() const {
    float v = autoCalibStartMinF_;
    if (v < 0.f) v = 0.f;
    if (v > 1439.f) v = 1439.f;
    return static_cast<uint16_t>(v + 0.5f);
}

uint16_t Config::autoCalibEndMin() const {
    float v = autoCalibEndMinF_;
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

void Config::setAutoCalibStartMin(uint16_t m) {
    float v = static_cast<float>(m);
    if (v != autoCalibStartMinF_) {
        autoCalibStartMinF_ = v;
        dirty_ = true;
    }
}

void Config::setAutoCalibEndMin(uint16_t m) {
    float v = static_cast<float>(m);
    if (v != autoCalibEndMinF_) {
        autoCalibEndMinF_ = v;
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

void Config::setAutoCalibTargetCapC(float v) {
    if (v <= 0.0f) v = 1.0f;
    if (v > 60.0f) v = 60.0f;
    if (v == autoCalibTargetCap_) return;
    autoCalibTargetCap_ = v;
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

void Config::setAutoCalibrationEnabled(bool v) {
    if (v == autoCalibrationEnabled_) return;
    autoCalibrationEnabled_ = v;
    dirty_ = true;
}

void Config::setReadyByTargetEpochUtc(uint64_t v) {
    if (v == readyByTargetEpochUtc_) return;
    readyByTargetEpochUtc_ = v;
    dirty_ = true;
}
