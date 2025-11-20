#pragma once

#include <Arduino.h>
#include <Preferences.h>

class Config {
public:
    Config();

    // Call once from setup()
    bool begin();      // opens NVS and loads values (or defaults)
    void load();       // reload from NVS
    void save() const; // write to NVS if dirty

    // Getters
    float targetTemp() const        { return targetTemp_; }
    float hysteresis() const        { return hysteresis_; }
    float heaterTaskDelayS() const  { return heaterTaskDelayS_; }
    uint16_t deadzoneStartMin() const;
    uint16_t deadzoneEndMin() const;
    float kFactor() const            { return kFactor_; }
    // Example boolean getters
    bool deadzoneEnabled() const     { return deadzoneEnabled_; }
    bool heaterTaskEnabled() const   { return heaterTaskEnabled_; }
    
    // Setters (mark config as dirty, but do not auto-save)
    void setTargetTemp(float v);
    void setHysteresis(float v);
    void setHeaterTaskDelayS(float v);
    void setDeadzoneStartMin(uint16_t m);
    void setDeadzoneEndMin(uint16_t m);
    void setKFactor(float v);
    // Boolean setters (mark dirty; persisted by save())
    void setDeadzoneEnabled(bool v);
    void setHeaterTaskEnabled(bool v);

private:
    // Not copyable
    Config(const Config&) = delete;
    Config& operator=(const Config&) = delete;

    // Descriptor for one float field
    struct FloatFieldDesc {
        const char*   key;          // NVS key
        float         defaultValue; // default if key missing
        float Config::* member;     // pointer to member
    };

    // Table of all float fields (defined in .cpp)
    static const FloatFieldDesc FLOAT_FIELDS[];

    // Descriptor for one boolean field
    struct BoolFieldDesc {
        const char*  key;            // NVS key
        bool         defaultValue;   // default if key missing
        bool  Config::* member;      // pointer to member
    };

    // Table of all boolean fields (defined in .cpp)
    static const BoolFieldDesc BOOL_FIELDS[];

    // These are modified even from save() const
    mutable Preferences prefs_;
    mutable bool        dirty_;

    // Actual stored values
    float  targetTemp_;
    float  hysteresis_;
    float  heaterTaskDelayS_;
    float  deadzoneStartMinF_;  // stored as float minutes
    float  deadzoneEndMinF_;
    float  kFactor_;

    // Example booleans (persisted via BOOL_FIELDS)
    bool   deadzoneEnabled_;
    bool   heaterTaskEnabled_;
};
