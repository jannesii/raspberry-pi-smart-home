#pragma once
#include <ArduinoJson.h>
#include <string>
#include <cmath>
#include <cstring>

class RelayTransport {
public:
    virtual ~RelayTransport() = default;
    virtual bool postRelay(const std::string& command) = 0;
};

// Highest shared actuator seam: both command transports use this executor.
class RelayExecutor {
public:
    static bool execute(JsonObjectConst command, double now, RelayTransport& transport, const char* bootId) {
        const char* action = command["action"] | "";
        if (strcmp(action, "turn_off") == 0) {
            std::string body;
            serializeJson(command, body);
            return transport.postRelay(body); // OFF never uses cooldown or clock gates.
        }
        if (strcmp(action, "turn_on") != 0) return false;
        if (strcmp(command["device_boot_id"] | "", bootId) != 0) return false;
        if (now < 1700000000 || !command["issued_at"].is<double>() ||
            !command["expires_at"].is<double>()) return false;
        double issued = command["issued_at"].as<double>();
        double expiry = command["expires_at"].as<double>();
        if (!std::isfinite(issued) || !std::isfinite(expiry) || issued > now ||
            expiry <= now || expiry > issued + 30) return false;
        if (!command["session_id"].isNull()) {
            const char* id = command["session_id"] | "";
            double finish = command["finish_at"].as<double>();
            if (!id[0] || !command["finish_at"].is<double>() || !std::isfinite(finish) ||
                finish <= now || finish > issued + 10800) return false;
        }
        std::string body;
        serializeJson(command, body);
        return transport.postRelay(body);
    }
};
