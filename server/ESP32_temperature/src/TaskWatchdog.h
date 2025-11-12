#pragma once

#include <Arduino.h>

namespace TaskWatchdog
{
    enum class TaskId : uint8_t
    {
        Sensor = 0,
        HttpServer = 1,
        Count
    };

    using RestartFn = void (*)();

    void init();
    void registerTask(TaskId id, const char *name, RestartFn restartFn, uint32_t timeoutMs);
    void heartbeat(TaskId id);
    void unregisterTask(TaskId id);
}
