#include "SensorTask.h"

#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <DHT_U.h>
#include <WiFi.h>
#include <time.h>
#include <sys/time.h>

#include "Poster.h"
#include "config.h"
#include "AppConfig.h"
#include "Metrics.h"
#include "StructuredLog.h"
#include "TaskWatchdog.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static Poster *gPoster = nullptr;
static TaskHandle_t gSensorTaskHandle = nullptr;
static SemaphoreHandle_t gDhtMutex = nullptr;

// DHT sensor instance
static DHT_Unified dht(DHTPIN, DHTTYPE);

static uint8_t dhtFailCount = 0;

static bool takeReading(float &t, float &h, String &err)
{
  if (!gDhtMutex)
  {
    gDhtMutex = xSemaphoreCreateMutex();
  }
  xSemaphoreTake(gDhtMutex, portMAX_DELAY);

  sensors_event_t event;
  t = NAN;
  h = NAN;

  dht.temperature().getEvent(&event);
  if (!isnan(event.temperature))
    t = event.temperature;

  dht.humidity().getEvent(&event);
  if (!isnan(event.relative_humidity))
    h = event.relative_humidity;

  if (isnan(t) || isnan(h))
  {
    dhtFailCount++;
    err.reserve(64);
    err = F("DHT read failed: ");
    if (isnan(t) && isnan(h))
      err += F("temp+hum");
    else if (isnan(t))
      err += F("temp");
    else if (isnan(h))
      err += F("hum");
    LOG_WARN(err);
    if ((dhtFailCount % 1) == 0)
    {
      LOG_INFO(F("Reinitializing DHT sensor..."));
      dht.begin();
    }
    xSemaphoreGive(gDhtMutex);
    Metrics::recordSensorRead(false, t, h);
    return false;
  }

  dhtFailCount = 0;
  xSemaphoreGive(gDhtMutex);
  Metrics::recordSensorRead(true, t, h);
  return true;
}

static bool readAndPost()
{
  float t = NAN, h = NAN;
  String err;
  bool ok = takeReading(t, h, err);
  if (!ok)
  {
    if (gPoster)
      (void)gPoster->postError(err);
    return false;
  }

  {
    String msg = F("Temperature: ");
    msg += String(t, 2);
    msg += F(" °C, Humidity: ");
    msg += String(h, 2);
    msg += F(" %");
    LOG_INFO(msg);
  }

  if (gPoster)
    return gPoster->postReading(t, h);
  return false;
}

static void SensorTask(void *pv)
{
  // Attempt to use UTC minute boundaries when time is available
  bool timeSynced = false;
  time_t nextPostEpoch = 0; // next UTC epoch second to post

  TickType_t lastWakeTick = xTaskGetTickCount();
  TickType_t nextPostTick = lastWakeTick;
  TickType_t lastSyncAttemptTick = lastWakeTick;

  auto readIntervalSeconds = []()
  {
    uint32_t value = AppConfig::get().getPostIntervalSeconds();
    if (value == 0)
      value = 1;
    return value;
  };

  auto computeIntervalMillis = [](uint32_t sec)
  {
    if (sec >= 4294967UL)
      return 0xFFFFFFFFUL; // clamp to avoid overflow
    return static_cast<unsigned long>(sec * 1000UL);
  };

  uint32_t intervalSec = readIntervalSeconds();
  bool alignToMinute = AppConfig::get().getAlignPostsToMinute();

  auto scheduleNextTick = [&](TickType_t nowTicks, bool logReason, const __FlashStringHelper *message)
  {
    uint32_t intervalMs = computeIntervalMillis(intervalSec);
    TickType_t scheduledTick = nowTicks + pdMS_TO_TICKS(intervalMs ? intervalMs : 1UL);

    if (timeSynced)
    {
      time_t nowEpoch = time(nullptr);
      time_t targetEpoch;
      if (alignToMinute)
      {
        targetEpoch = ((nowEpoch / intervalSec) + 1) * static_cast<time_t>(intervalSec);
      }
      else
      {
        targetEpoch = nowEpoch + static_cast<time_t>(intervalSec);
      }
      if (targetEpoch <= nowEpoch)
      {
        targetEpoch = nowEpoch + 1;
      }
      nextPostEpoch = targetEpoch;

      struct timeval tv;
      if (gettimeofday(&tv, nullptr) == 0)
      {
        uint64_t nowMs = (static_cast<uint64_t>(tv.tv_sec) * 1000ULL) + (tv.tv_usec / 1000ULL);
        uint64_t targetMs = static_cast<uint64_t>(targetEpoch) * 1000ULL;
        uint64_t deltaMs = (targetMs > nowMs) ? (targetMs - nowMs) : 0ULL;
        if (deltaMs == 0ULL)
        {
          deltaMs = intervalMs ? intervalMs : 1ULL;
        }
        if (deltaMs > 0xFFFFFFFFULL)
        {
          deltaMs = 0xFFFFFFFFULL;
        }
        scheduledTick = nowTicks + pdMS_TO_TICKS(static_cast<uint32_t>(deltaMs));
      }

      if (logReason)
      {
        String msg = F("Next measurement (epoch): ");
        msg += static_cast<long>(nextPostEpoch);
        LOG_DEBUG(msg);
        if (message)
        {
          LOG_DEBUG(message);
        }
      }
    }
    else
    {
      nextPostEpoch = 0;
      if (logReason && message)
      {
        LOG_DEBUG(message);
      }
    }

    return scheduledTick;
  };

  // Initialize sensor
  dht.begin();
  if (!gDhtMutex)
    gDhtMutex = xSemaphoreCreateMutex();
  LOG_INFO(F("DHT sensor initialized (task)"));

  TaskWatchdog::registerTask(TaskWatchdog::TaskId::Sensor, "SensorPostTask", restartSensorTask, 60000);

  // Take one immediate measurement after boot
  (void)readAndPost();

  // Check if time is available yet (non-blocking)
  struct tm ti;
  TickType_t initialTick = xTaskGetTickCount();
  if (getLocalTime(&ti, 1))
  {
    timeSynced = true;
    nextPostTick = scheduleNextTick(initialTick, true, F("Scheduling cadence initialized (time-synced)."));
  }
  else
  {
    nextPostTick = scheduleNextTick(initialTick, true, F("Scheduling cadence initialized (pre time-sync)."));
  }

  for (;;)
  {
    TaskWatchdog::heartbeat(TaskWatchdog::TaskId::Sensor);
    // Refresh configuration periodically so runtime updates apply without reboot.
    uint32_t latestInterval = readIntervalSeconds();
    bool latestAlign = AppConfig::get().getAlignPostsToMinute();
    if (latestInterval != intervalSec || latestAlign != alignToMinute)
    {
      intervalSec = latestInterval;
      alignToMinute = latestAlign;
      TickType_t nowTicks = xTaskGetTickCount();
      nextPostTick = scheduleNextTick(nowTicks, true, timeSynced ? F("Scheduling cadence updated (time-synced).") : F("Scheduling cadence updated (pre time-sync)."));
    }

    bool wifiConnected = (WiFi.status() == WL_CONNECTED);

    if (!timeSynced && wifiConnected)
    {
      TickType_t now = xTaskGetTickCount();
      if ((now - lastSyncAttemptTick) >= pdMS_TO_TICKS(10000UL))
      {
        lastSyncAttemptTick = now;
        if (getLocalTime(&ti, 1))
        {
          timeSynced = true;
          nextPostTick = scheduleNextTick(now, true, F("Time synchronized; switching to epoch-based schedule."));
        }
      }
    }

    TickType_t nowTicks = xTaskGetTickCount();
    if ((int32_t)(nowTicks - nextPostTick) >= 0)
    {
      if (wifiConnected)
      {
        (void)readAndPost();
      }
      TickType_t afterRead = xTaskGetTickCount();
      nextPostTick = scheduleNextTick(afterRead, false, nullptr);
      continue;
    }

    TickType_t waitTicks = nextPostTick - nowTicks;
    const TickType_t kMaxSleepTicks = pdMS_TO_TICKS(1000UL);
    if (waitTicks > kMaxSleepTicks)
    {
      waitTicks = kMaxSleepTicks;
    }
    if (waitTicks == 0)
    {
      waitTicks = 1;
    }
    vTaskDelayUntil(&lastWakeTick, waitTicks);
  }
}

void startSensorTask(Poster *poster)
{
  gPoster = poster;
  xTaskCreate(
      SensorTask,
      "SensorPostTask",
      8192,
      nullptr,
      1,
      &gSensorTaskHandle);
}

// Helpers for task control/status
extern "C"
{
  TaskHandle_t sensorTaskHandle() { return gSensorTaskHandle; }
  void restartSensorTask()
  {
    TaskHandle_t h = gSensorTaskHandle;
    gSensorTaskHandle = nullptr;
    TaskWatchdog::unregisterTask(TaskWatchdog::TaskId::Sensor);
    if (h)
    {
      vTaskDelete(h);
      vTaskDelay(pdMS_TO_TICKS(10));
    }
    startSensorTask(gPoster);
  }
}

bool sensorTakeReading(float &temperatureC, float &humidityPct, String &errorOut)
{
  return takeReading(temperatureC, humidityPct, errorOut);
}
