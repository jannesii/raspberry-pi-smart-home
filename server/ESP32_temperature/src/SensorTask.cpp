#include "SensorTask.h"

#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <DHT_U.h>
#include <WiFi.h>
#include <time.h>

#include "Poster.h"
#include "config.h"
#include "AppConfig.h"

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
    Serial.println(err);
    if ((dhtFailCount % 1) == 0)
    {
      Serial.println(F("Reinitializing DHT sensor..."));
      dht.begin();
    }
    xSemaphoreGive(gDhtMutex);
    return false;
  }

  dhtFailCount = 0;
  xSemaphoreGive(gDhtMutex);
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

  Serial.print(F("Temperature: "));
  Serial.print(t, 2);
  Serial.println(F(" °C"));
  Serial.print(F("Humidity: "));
  Serial.print(h, 2);
  Serial.println(F(" %"));

  if (gPoster)
    return gPoster->postReading(t, h);
  return false;
}

static void SensorTask(void *pv)
{
  // Attempt to use UTC minute boundaries when time is available
  bool timeSynced = false;
  time_t nextPostEpoch = 0; // next UTC epoch second to post

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
  unsigned long nextPostMillis = millis() + computeIntervalMillis(intervalSec); // fallback cadence

  // Initialize sensor
  dht.begin();
  if (!gDhtMutex)
    gDhtMutex = xSemaphoreCreateMutex();
  Serial.println(F("DHT sensor initialized (task)"));

  // Take one immediate measurement after boot
  (void)readAndPost();

  // Check if time is available yet (non-blocking)
  struct tm ti;
  if (getLocalTime(&ti, 1))
  {
    timeSynced = true;
    time_t now = time(nullptr);
    if (alignToMinute)
    {
      nextPostEpoch = ((now / intervalSec) + 1) * intervalSec;
    }
    else
    {
      nextPostEpoch = now + intervalSec;
    }
    Serial.print(F("Next measurement (epoch): "));
    Serial.println((long)nextPostEpoch);
  }

  for (;;)
  {
    // Refresh configuration periodically so runtime updates apply without reboot.
    uint32_t latestInterval = readIntervalSeconds();
    bool latestAlign = AppConfig::get().getAlignPostsToMinute();
    if (latestInterval != intervalSec || latestAlign != alignToMinute)
    {
      intervalSec = latestInterval;
      alignToMinute = latestAlign;
      if (timeSynced)
      {
        time_t now = time(nullptr);
        if (alignToMinute)
        {
          nextPostEpoch = ((now / intervalSec) + 1) * intervalSec;
        }
        else
        {
          nextPostEpoch = now + intervalSec;
        }
        Serial.println(F("Scheduling cadence updated (time-synced)."));
      }
      else
      {
        nextPostMillis = millis() + computeIntervalMillis(intervalSec);
        Serial.println(F("Scheduling cadence updated (pre time-sync)."));
      }
    }

    if (WiFi.status() == WL_CONNECTED)
    {
      if (timeSynced)
      {
        time_t now = time(nullptr);
        if (now >= nextPostEpoch)
        {
          (void)readAndPost();
          if (alignToMinute)
          {
            nextPostEpoch = ((now / intervalSec) + 1) * intervalSec;
          }
          else
          {
            nextPostEpoch = now + intervalSec;
          }
        }
      }
      else
      {
        if (millis() >= nextPostMillis)
        {
          (void)readAndPost();
          nextPostMillis = millis() + computeIntervalMillis(intervalSec);
        }

        // Retry time sync occasionally without blocking the CPU
        static unsigned long lastSyncTry = 0;
        if (millis() - lastSyncTry > 10000UL)
        {
          lastSyncTry = millis();
          if (getLocalTime(&ti, 1))
          {
            timeSynced = true;
            time_t now = time(nullptr);
            if (alignToMinute)
            {
              nextPostEpoch = ((now / intervalSec) + 1) * intervalSec;
            }
            else
            {
              nextPostEpoch = now + intervalSec;
            }
            Serial.println(F("Time synchronized; switching to epoch-based schedule."));
          }
        }
      }
    }

    vTaskDelay(pdMS_TO_TICKS(200));
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
