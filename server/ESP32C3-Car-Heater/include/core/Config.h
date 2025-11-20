#pragma once

#include <Arduino.h>
#include <Preferences.h>

class Config
{
public:
    Config();

    // Call once from setup()
    bool begin();      // opens NVS and loads values (or defaults)
    void load();       // reload from NVS
    void save() const; // write to NVS if dirty

    // float getters
    float targetTemp() const { return targetTemp_; }
    float hysteresis() const { return hysteresis_; }
    float heaterTaskDelayS() const { return heaterTaskDelayS_; }
    uint16_t deadzoneStartMin() const;
    uint16_t deadzoneEndMin() const;
    float kFactor() const { return kFactor_; }
    float readyByTargetTemp() const { return readyByTargetTemp_; }
    float autoCalibTargetCapC() const { return autoCalibTargetCap_; }

    // Boolean getters
    bool deadzoneEnabled() const { return deadzoneEnabled_; }
    bool heaterTaskEnabled() const { return heaterTaskEnabled_; }
    bool readyByActive() const { return readyByActive_; }
    bool autoCalibrationEnabled() const { return autoCalibrationEnabled_; }
    uint16_t autoCalibStartMin() const;
    uint16_t autoCalibEndMin() const;

    // uint64 getters
    uint64_t readyByTargetEpochUtc() const { return readyByTargetEpochUtc_; }

    // Setters (mark config as dirty, but do not auto-save)
    // Float setters
    void setTargetTemp(float v);
    void setHysteresis(float v);
    void setHeaterTaskDelayS(float v);
    void setDeadzoneStartMin(uint16_t m);
    void setDeadzoneEndMin(uint16_t m);
    void setKFactor(float v);
    void setReadyByTargetTemp(float v);
    void setAutoCalibTargetCapC(float v);
    // Boolean setters
    void setDeadzoneEnabled(bool v);
    void setHeaterTaskEnabled(bool v);
    void setReadyByActive(bool v);
    void setAutoCalibrationEnabled(bool v);
    void setAutoCalibStartMin(uint16_t m);
    void setAutoCalibEndMin(uint16_t m);
    // uint64 setters
    void setReadyByTargetEpochUtc(uint64_t v);

private:
    // Not copyable
    Config(const Config &) = delete;
    Config &operator=(const Config &) = delete;

    // Descriptor for one float field
    struct FloatFieldDesc
    {
        const char *key;       // NVS key
        float defaultValue;    // default if key missing
        float Config::*member; // pointer to member
    };

    // Table of all float fields (defined in .cpp)
    static const FloatFieldDesc FLOAT_FIELDS[];

    // Descriptor for one boolean field
    struct BoolFieldDesc
    {
        const char *key;      // NVS key
        bool defaultValue;    // default if key missing
        bool Config::*member; // pointer to member
    };

    // Table of all boolean fields (defined in .cpp)
    static const BoolFieldDesc BOOL_FIELDS[];

    // Descriptor for one uint64 field
    struct Uint64FieldDesc
    {
        const char *key;          // NVS key
        uint64_t defaultValue;    // default if key missing
        uint64_t Config::*member; // pointer to member
    };

    // Table of all uint64 fields (defined in .cpp)
    static const Uint64FieldDesc UINT64_FIELDS[];

    // These are modified even from save() const
    mutable Preferences prefs_;
    mutable bool dirty_;

    // Actual stored values
    float targetTemp_;
    float hysteresis_;
    float heaterTaskDelayS_;
    float deadzoneStartMinF_; // stored as float minutes
    float deadzoneEndMinF_;
    float kFactor_;
    float readyByTargetTemp_;
    float autoCalibStartMinF_;
    float autoCalibEndMinF_;
    float autoCalibTargetCap_;

    // booleans (persisted via BOOL_FIELDS)
    bool deadzoneEnabled_;
    bool heaterTaskEnabled_;
    bool readyByActive_;
    bool autoCalibrationEnabled_;

    // uint64s (persisted via UINT64_FIELDS)
    uint64_t readyByTargetEpochUtc_;
};
