#include <Arduino.h>
#include <WiFi.h>
#include <time.h>

#include "config.h"
#include "Poster.h"
#include "SensorTask.h"
#include "HttpServerTask.h"
#include "AppConfig.h"

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

  Serial.println(F("Factory reset button held. Hold to confirm..."));
  unsigned long start = millis();
  while (digitalRead(FACTORY_RESET_PIN) == FACTORY_RESET_ACTIVE_LEVEL)
  {
    if (millis() - start >= FACTORY_RESET_HOLD_MS)
    {
      Serial.println(F("Factory reset triggered via button."));
      AppConfig::get().factoryReset();
      Serial.println(F("Restarting after factory reset..."));
      delay(200);
      ESP.restart();
    }
    delay(25);
  }

  Serial.println(F("Factory reset aborted (button released early)."));
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

void setup()
{
  Serial.begin(115200);
  delay(2000);
  Serial.println();
  Serial.println(F("Booting..."));

  maybeFactoryResetOnBoot();

  IPAddress local_IP(192, 168, 10, 124);
  IPAddress gateway(192, 168, 10, 1);
  IPAddress subnet(255, 255, 255, 0);
  IPAddress dns1(1, 1, 1, 1); // optional but recommended
  IPAddress dns2(9, 9, 9, 9); // optional

  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true); // clear old DHCP lease if any
  delay(100);

  // Connect WiFi (blocking until connected)
  Serial.print(F("Connecting to "));
  Serial.println(AppConfig::get().getWifiSSID());
  WiFi.begin(AppConfig::get().getWifiSSID().c_str(), AppConfig::get().getWifiPassword().c_str());
  while (WiFi.status() != WL_CONNECTED)
  {
    delay(500);
    Serial.print('.');
  }
  Serial.println();
  Serial.println(F("WiFi connected."));
  Serial.print(F("IP address: "));
  Serial.println(WiFi.localIP());

  // Configure NTP (UTC). Time sync attempts will be handled by tasks.
  configTime(0 /*gmtOffset*/, 0 /*dstOffset*/, ntp1, ntp2, ntp3);

  // Start tasks
  startHttpServerTask();
  startSensorTask(&gPoster);
}

void loop()
{
  // Nothing to do here; tasks handle all work.
  delay(1000);
}
