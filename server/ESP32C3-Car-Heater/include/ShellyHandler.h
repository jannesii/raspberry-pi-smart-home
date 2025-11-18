#pragma once

#include <Arduino.h>

class ShellyHandler
{
public:
    ShellyHandler(String ipAddress);

    // Returns true on success (HTTP 2xx), false on failure
    bool switchOn();
    bool switchOff();
    bool toggle();
    
    // Query current status from Shelly.
    // Returns true if the HTTP request + parsing succeeded, and
    // writes the result into isOn (true = ON, false = OFF).
    bool getStatus(bool &isOn, bool verbose = true);
    
    bool reboot();
    bool ping();
private:
    bool sendSwitchRequest(bool on);

    String baseUrl_; // e.g. "http://192.168.33.1/rpc/Switch.Set?id=0&on="
    String ip_;
};
