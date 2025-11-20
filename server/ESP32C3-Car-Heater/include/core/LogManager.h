// LogManager.h
#pragma once

#include <Arduino.h>
#include <Preferences.h>
#include <functional>

class LogManager {
public:
    LogManager();

    using LogCallback = std::function<void(const String &)>;

    void setCallback(LogCallback cb) { callback_ = cb; }

    bool begin();  // call in setup()

    // Append a log line (no newline needed)
    void append(const String& line);

    // Dump all logs in time order to Serial (for debugging)
    void dumpToSerial() const;

    String toStringNewestFirst(uint16_t maxLines = 0) const;

    void clear();

private:
    LogCallback callback_;
    
    static constexpr const char* NAMESPACE    = "logs";
    static constexpr const char* KEY_HEAD     = "head";
    static constexpr const char* KEY_COUNT    = "count";
    static constexpr uint16_t    MAX_ENTRIES  = 400; // tune this

    mutable Preferences prefs_;
    uint16_t head_;   // next index to write [0..MAX_ENTRIES-1]
    uint16_t count_;  // number of stored entries [0..MAX_ENTRIES]

    String makeKey(uint16_t index) const;
};
