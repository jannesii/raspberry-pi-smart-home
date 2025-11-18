#pragma once

class Thermostat {
public:
    Thermostat(float targetTemp, float hysteresis);

    bool update(float currentTemp);

    void setTarget(float targetTemp);
    void setHysteresis(float hysteresis);

    float target() const;
    float hysteresis() const;
    bool isHeaterOn() const;

private:
    float targetTemp_;
    float hysteresis_;
    bool  heaterOn_;
    bool  initialized_;
};
