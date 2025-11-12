#include "StructuredLog.h"

#include <stdarg.h>
#include <string.h>
#include <pgmspace.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

namespace StructuredLog
{
    namespace
    {
        constexpr size_t kCapacity = 64;
        constexpr size_t kMessageMaxLen = sizeof(Entry::message);

        Entry gRing[kCapacity];
        size_t gWriteIndex = 0;
        size_t gCount = 0;
        Level gCurrentLevel = Level::Info;
        SemaphoreHandle_t gMutex = nullptr;

        const char *const kLevelNames[] = {"error", "warn", "info", "debug"};

        SemaphoreHandle_t ensureMutex()
        {
            if (!gMutex)
            {
                gMutex = xSemaphoreCreateMutex();
            }
            return gMutex;
        }

        bool shouldLog(Level level)
        {
            return static_cast<uint8_t>(level) <= static_cast<uint8_t>(gCurrentLevel);
        }

        void writeEntry(const Entry &entry)
        {
            if (!ensureMutex())
            {
                return;
            }
            if (xSemaphoreTake(gMutex, portMAX_DELAY) != pdTRUE)
            {
                return;
            }
            gRing[gWriteIndex] = entry;
            gWriteIndex = (gWriteIndex + 1) % kCapacity;
            if (gCount < kCapacity)
            {
                ++gCount;
            }
            xSemaphoreGive(gMutex);

            const char *levelText = levelName(entry.level);
            Serial.printf("[%s][%lu] %s\n",
                          levelText,
                          static_cast<unsigned long>(entry.timestampMs),
                          entry.message);
        }

        void copyToEntry(Level level, const char *message)
        {
            if (!shouldLog(level))
            {
                return;
            }

            Entry entry;
            entry.timestampMs = millis();
            entry.level = level;
            strncpy(entry.message, message, kMessageMaxLen - 1);
            entry.message[kMessageMaxLen - 1] = '\0';
            writeEntry(entry);
        }
    } // namespace

    void init()
    {
        ensureMutex();
    }

    void setLevel(Level level)
    {
        gCurrentLevel = level;
    }

    Level getLevel()
    {
        return gCurrentLevel;
    }

    const char *levelName(Level level)
    {
        size_t idx = static_cast<size_t>(level);
        if (idx >= (sizeof(kLevelNames) / sizeof(kLevelNames[0])))
        {
            return "unknown";
        }
        return kLevelNames[idx];
    }

    bool levelFromString(const String &text, Level &outLevel)
    {
        String lowered = text;
        lowered.toLowerCase();
        for (size_t i = 0; i < (sizeof(kLevelNames) / sizeof(kLevelNames[0])); ++i)
        {
            if (lowered == kLevelNames[i])
            {
                outLevel = static_cast<Level>(i);
                return true;
            }
        }
        return false;
    }

    void clear()
    {
        if (!ensureMutex())
        {
            return;
        }
        if (xSemaphoreTake(gMutex, portMAX_DELAY) != pdTRUE)
        {
            return;
        }
        gWriteIndex = 0;
        gCount = 0;
        memset(gRing, 0, sizeof(gRing));
        xSemaphoreGive(gMutex);
    }

    void log(Level level, const char *message)
    {
        copyToEntry(level, message ? message : "");
    }

    void log(Level level, const __FlashStringHelper *message)
    {
        if (!message)
        {
            copyToEntry(level, "");
            return;
        }
        char buffer[kMessageMaxLen];
        strncpy_P(buffer, reinterpret_cast<const char *>(message), kMessageMaxLen - 1);
        buffer[kMessageMaxLen - 1] = '\0';
        copyToEntry(level, buffer);
    }

    void log(Level level, const String &message)
    {
        copyToEntry(level, message.c_str());
    }

    void logf(Level level, const char *fmt, ...)
    {
        if (!shouldLog(level))
        {
            return;
        }
        char buffer[kMessageMaxLen];
        va_list args;
        va_start(args, fmt);
        vsnprintf(buffer, kMessageMaxLen, fmt ? fmt : "", args);
        va_end(args);
        copyToEntry(level, buffer);
    }

    size_t snapshot(Entry *out, size_t maxEntries)
    {
        if (!out || maxEntries == 0)
        {
            return 0;
        }
        if (!ensureMutex())
        {
            return 0;
        }
        if (xSemaphoreTake(gMutex, portMAX_DELAY) != pdTRUE)
        {
            return 0;
        }
        size_t available = gCount;
        size_t toCopy = (available < maxEntries) ? available : maxEntries;
        size_t start = (gWriteIndex + kCapacity - available % kCapacity) % kCapacity;
        for (size_t i = 0; i < toCopy; ++i)
        {
            size_t idx = (start + i) % kCapacity;
            out[i] = gRing[idx];
        }
        xSemaphoreGive(gMutex);
        return toCopy;
    }
}
