#include "TaskWatchdog.h"

#include <Arduino.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "StructuredLog.h"

namespace TaskWatchdog
{
    namespace
    {
        struct WatchedTask
        {
            const char *name = nullptr;
            RestartFn restartFn = nullptr;
            TickType_t timeoutTicks = 0;
            TickType_t lastHeartbeat = 0;
            TaskHandle_t handle = nullptr;
            bool registered = false;
        };

        static WatchedTask gTasks[static_cast<size_t>(TaskId::Count)];
        static SemaphoreHandle_t gMutex = nullptr;
        static TaskHandle_t gMonitorTask = nullptr;

        SemaphoreHandle_t ensureMutex()
        {
            if (!gMutex)
            {
                gMutex = xSemaphoreCreateMutex();
            }
            return gMutex;
        }

        bool copyTasks(WatchedTask (&out)[static_cast<size_t>(TaskId::Count)])
        {
            if (!ensureMutex())
            {
                return false;
            }
            if (xSemaphoreTake(gMutex, pdMS_TO_TICKS(25)) != pdTRUE)
            {
                return false;
            }
            memcpy(out, gTasks, sizeof(gTasks));
            xSemaphoreGive(gMutex);
            return true;
        }

        bool markTaskUnregistered(size_t index, TickType_t expectedHeartbeat)
        {
            if (!ensureMutex())
            {
                return false;
            }
            if (xSemaphoreTake(gMutex, pdMS_TO_TICKS(25)) != pdTRUE)
            {
                return false;
            }
            WatchedTask &slot = gTasks[index];
            if (!slot.registered || slot.lastHeartbeat != expectedHeartbeat)
            {
                xSemaphoreGive(gMutex);
                return false;
            }
            slot.registered = false;
            slot.handle = nullptr;
            slot.lastHeartbeat = xTaskGetTickCount();
            xSemaphoreGive(gMutex);
            return true;
        }

        void clearTask(size_t index)
        {
            if (!ensureMutex())
            {
                return;
            }
            if (xSemaphoreTake(gMutex, portMAX_DELAY) != pdTRUE)
            {
                return;
            }
            WatchedTask &slot = gTasks[index];
            slot.registered = false;
            slot.handle = nullptr;
            slot.lastHeartbeat = xTaskGetTickCount();
            xSemaphoreGive(gMutex);
        }

        void watchdogTask(void *pv)
        {
            (void)pv;
            WatchedTask local[static_cast<size_t>(TaskId::Count)];
            for (;;)
            {
                vTaskDelay(pdMS_TO_TICKS(1000));
                if (!copyTasks(local))
                {
                    continue;
                }
                TickType_t now = xTaskGetTickCount();
                for (size_t i = 0; i < static_cast<size_t>(TaskId::Count); ++i)
                {
                    WatchedTask &slot = local[i];
                    if (!slot.registered || slot.timeoutTicks == 0)
                    {
                        continue;
                    }

                    if (slot.handle)
                    {
#if defined(INCLUDE_eTaskGetState) && (INCLUDE_eTaskGetState == 1)
                        eTaskState state = eTaskGetState(slot.handle);
                        if (state == eSuspended)
                        {
                            continue;
                        }
                        if (state == eDeleted)
                        {
                            markTaskUnregistered(i, slot.lastHeartbeat);
                            continue;
                        }
#endif
                    }

                    TickType_t elapsed = now - slot.lastHeartbeat;
                    if (static_cast<int32_t>(elapsed) < 0)
                    {
                        continue;
                    }

                    if (elapsed > slot.timeoutTicks)
                    {
                        if (markTaskUnregistered(i, slot.lastHeartbeat))
                        {
                            String msg = F("[Watchdog] Restarting task: ");
                            msg += (slot.name ? slot.name : "<unnamed>");
                            LOG_WARN(msg);
                            if (slot.restartFn)
                            {
                                slot.restartFn();
                            }
                        }
                    }
                }
            }
        }
    } // namespace

    void init()
    {
        if (!ensureMutex())
        {
            return;
        }
        if (!gMonitorTask)
        {
            memset(gTasks, 0, sizeof(gTasks));
            xTaskCreate(
                watchdogTask,
                "TaskWatchdog",
                4096,
                nullptr,
                1,
                &gMonitorTask);
        }
    }

    void registerTask(TaskId id, const char *name, RestartFn restartFn, uint32_t timeoutMs)
    {
        if (!ensureMutex())
        {
            return;
        }
        TickType_t now = xTaskGetTickCount();
        TickType_t timeoutTicks = timeoutMs == 0 ? 0 : pdMS_TO_TICKS(timeoutMs);
        if (timeoutTicks == 0 && timeoutMs > 0)
        {
            timeoutTicks = 1;
        }

        if (xSemaphoreTake(gMutex, portMAX_DELAY) != pdTRUE)
        {
            return;
        }
        WatchedTask &slot = gTasks[static_cast<size_t>(id)];
        slot.name = name;
        slot.restartFn = restartFn;
        slot.timeoutTicks = timeoutTicks;
        slot.lastHeartbeat = now;
        slot.handle = xTaskGetCurrentTaskHandle();
        slot.registered = true;
        xSemaphoreGive(gMutex);

        String msg = F("[Watchdog] Registered task: ");
        msg += (name ? name : "<unnamed>");
        LOG_INFO(msg);
    }

    void heartbeat(TaskId id)
    {
        if (!gMutex)
        {
            return;
        }
        TickType_t now = xTaskGetTickCount();
        if (xSemaphoreTake(gMutex, pdMS_TO_TICKS(5)) != pdTRUE)
        {
            return;
        }
        WatchedTask &slot = gTasks[static_cast<size_t>(id)];
        if (slot.registered)
        {
            slot.lastHeartbeat = now;
            slot.handle = xTaskGetCurrentTaskHandle();
        }
        xSemaphoreGive(gMutex);
    }

    void unregisterTask(TaskId id)
    {
        clearTask(static_cast<size_t>(id));
    }
}
