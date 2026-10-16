#pragma once
#include <ArduinoJson.h>
#include <string>
#include <cmath>
#include <cstring>

class RelayTransport {
public:
    virtual ~RelayTransport() = default;
    virtual bool postRelay(const std::string& command, std::string* note = nullptr) = 0;
};

// Highest shared actuator seam: both command transports use this executor.
class RelayExecutor {
public:
    static const char* rejectionReason(JsonObjectConst command, double now, const char* bootId) {
        if (strcmp(command["action"] | "", "turn_on") != 0) return "unknown relay action";
        if (strcmp(command["device_boot_id"] | "", bootId) != 0) return "obsolete ESP32 boot token";
        if (now < 1700000000) return "device clock unavailable";
        if (!command["issued_at"].is<double>() || !command["expires_at"].is<double>())
            return "command lifetime missing";
        double issued = command["issued_at"].as<double>();
        double expiry = command["expires_at"].as<double>();
        if (!std::isfinite(issued) || !std::isfinite(expiry)) return "invalid command lifetime";
        if (issued > now + 2) return "device clock behind command issue time";
        // Accept bounded rounding lag, but expire early so stale ON gains no time.
        if (expiry <= now + 2 || expiry > issued + 30) return "command lifetime expired or invalid";
        if (!command["session_id"].isNull()) {
            const char* id = command["session_id"] | "";
            double finish = command["finish_at"].as<double>();
            if (!id[0] || !command["finish_at"].is<double>() || !std::isfinite(finish) ||
                finish <= now + 2 || finish > issued + 10800) return "original finish invalid or expired";
        }
        return nullptr;
    }

    static bool execute(JsonObjectConst command, double now, RelayTransport& transport,
                        const char* bootId, std::string* note = nullptr) {
        if (strcmp(command["action"] | "", "turn_off") != 0) {
            const char* rejection = rejectionReason(command, now, bootId);
            if (rejection) {
                if (note) *note = rejection;
                return false;
            }
        }
        std::string body;
        serializeJson(command, body);
        // OFF never uses cooldown, boot token or clock gates.
        return transport.postRelay(body, note);
    }
};
