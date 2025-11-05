#include <Arduino.h>
#include <WiFi.h>
#include <time.h>

#include "config.h"
#include "Poster.h"
#include "SensorTask.h"
#include "HttpServerTask.h"
#include "AppConfig.h"

// WiFi credentials come from AppConfig defaults, but can be updated at runtime

// NTP servers (UTC)
static const char* ntp1 = "pool.ntp.org";
static const char* ntp2 = "time.nist.gov";
static const char* ntp3 = "time.google.com";

// Global poster instance
static Poster gPoster;

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println();
  Serial.println(F("Booting..."));

  // Connect WiFi (blocking until connected)
  Serial.print(F("Connecting to "));
  Serial.println(AppConfig::get().getWifiSSID());
  WiFi.begin(AppConfig::get().getWifiSSID().c_str(), AppConfig::get().getWifiPassword().c_str());
  while (WiFi.status() != WL_CONNECTED) {
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

void loop() {
  // Nothing to do here; tasks handle all work.
  delay(1000);
}
