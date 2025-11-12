#include <Arduino.h>
#include <WiFi.h>
#include <time.h>
#include <esp_system.h>

#include "config.h"
#include "Poster.h"
#include "SensorTask.h"
#include "HttpServerTask.h"
#include "AppConfig.h"
#include "WifiManager.h"
#include "StructuredLog.h"
#include "TaskWatchdog.h"

// WiFi credentials come from AppConfig defaults, but can be updated at runtime

#ifdef FACTORY_RESET_PIN
#ifndef FACTORY_RESET_ACTIVE_LEVEL
#define FACTORY_RESET_ACTIVE_LEVEL LOW
#endif
#ifndef FACTORY_RESET_PIN_MODE
#define FACTORY_RESET_PIN_MODE INPUT_PULLUP
#endif
#ifndef FACTORY_RESET_HOLD_MS
#define FACTORY_RESET_HOLD_MS 3000
#endif

static void maybeFactoryResetOnBoot()
{
  pinMode(FACTORY_RESET_PIN, FACTORY_RESET_PIN_MODE);
  delay(5);
  if (digitalRead(FACTORY_RESET_PIN) != FACTORY_RESET_ACTIVE_LEVEL)
  {
    return;
  }

  LOG_WARN(F("Factory reset button held. Hold to confirm..."));
  unsigned long start = millis();
  while (digitalRead(FACTORY_RESET_PIN) == FACTORY_RESET_ACTIVE_LEVEL)
  {
    if (millis() - start >= FACTORY_RESET_HOLD_MS)
    {
      LOG_WARN(F("Factory reset triggered via button."));
      AppConfig::get().factoryReset();
      LOG_WARN(F("Restarting after factory reset..."));
      delay(200);
      ESP.restart();
    }
    delay(25);
  }

  LOG_INFO(F("Factory reset aborted (button released early)."));
}
#else
static void maybeFactoryResetOnBoot() {}
#endif

// NTP servers (UTC)
static const char *ntp1 = "pool.ntp.org";
static const char *ntp2 = "time.nist.gov";
static const char *ntp3 = "time.google.com";

// Global poster instance
static Poster gPoster;

static const char *resetReasonLabel(esp_reset_reason_t reason)
{
  switch (reason)
  {
  case ESP_RST_UNKNOWN:
    return "UNKNOWN";
  case ESP_RST_POWERON:
    return "POWERON";
  case ESP_RST_EXT:
    return "EXT";
  case ESP_RST_SW:
    return "SW";
  case ESP_RST_PANIC:
    return "PANIC";
  case ESP_RST_INT_WDT:
    return "INT_WDT";
  case ESP_RST_TASK_WDT:
    return "TASK_WDT";
  case ESP_RST_WDT:
    return "WDT";
  case ESP_RST_DEEPSLEEP:
    return "DEEPSLEEP";
  case ESP_RST_BROWNOUT:
    return "BROWNOUT";
  case ESP_RST_SDIO:
    return "SDIO";
  default:
    return "OTHER";
  }
}

void setup()
{
  Serial.begin(115200);
  delay(2000);
  StructuredLog::init();
  StructuredLog::setLevel(AppConfig::get().getLogLevel());

  LOG_INFO(F("Booting..."));
  {
    String msg = F("Reset reason: ");
    msg += resetReasonLabel(esp_reset_reason());
    LOG_INFO(msg);
  }

  maybeFactoryResetOnBoot();

  TaskWatchdog::init();

  wifiManagerInit();

  const unsigned long wifiWaitMs = 15000UL;
  unsigned long startWait = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - startWait) < wifiWaitMs)
  {
    wifiManagerLoop();
    delay(50);
  }
  if (WiFi.status() == WL_CONNECTED)
  {
    String msg = F("Initial WiFi connection established: ");
    msg += WiFi.localIP().toString();
    LOG_INFO(msg);
  }
  else
  {
    LOG_WARN(F("Initial WiFi connect timed out; continuing without link."));
  }

  // Configure NTP (UTC). Time sync attempts will be handled by tasks.
  configTime(0 /*gmtOffset*/, 0 /*dstOffset*/, ntp1, ntp2, ntp3);

  // Start tasks
  startHttpServerTask();
  startSensorTask(&gPoster);
}

void loop()
{
  wifiManagerLoop();
  delay(100);
}
