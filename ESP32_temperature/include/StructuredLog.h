#pragma once

#include <Arduino.h>

namespace StructuredLog
{
    enum class Level : uint8_t
    {
        Error = 0,
        Warn = 1,
        Info = 2,
        Debug = 3
    };

    struct Entry
    {
        uint32_t timestampMs;
        Level level;
        char message[160];
    };

    void init();
    void setLevel(Level level);
    Level getLevel();
    const char *levelName(Level level);
    bool levelFromString(const String &text, Level &outLevel);
    void clear();

    void log(Level level, const char *message);
    void log(Level level, const __FlashStringHelper *message);
    void log(Level level, const String &message);
    void logf(Level level, const char *fmt, ...);

    size_t snapshot(Entry *out, size_t maxEntries);
}

#define LOG_ERROR(message) ::StructuredLog::log(::StructuredLog::Level::Error, message)
#define LOG_WARN(message) ::StructuredLog::log(::StructuredLog::Level::Warn, message)
#define LOG_INFO(message) ::StructuredLog::log(::StructuredLog::Level::Info, message)
#define LOG_DEBUG(message) ::StructuredLog::log(::StructuredLog::Level::Debug, message)

#define LOGF_ERROR(fmt, ...) ::StructuredLog::logf(::StructuredLog::Level::Error, fmt, ##__VA_ARGS__)
#define LOGF_WARN(fmt, ...) ::StructuredLog::logf(::StructuredLog::Level::Warn, fmt, ##__VA_ARGS__)
#define LOGF_INFO(fmt, ...) ::StructuredLog::logf(::StructuredLog::Level::Info, fmt, ##__VA_ARGS__)
#define LOGF_DEBUG(fmt, ...) ::StructuredLog::logf(::StructuredLog::Level::Debug, fmt, ##__VA_ARGS__)
