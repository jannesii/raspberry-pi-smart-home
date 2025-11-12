#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

// Central runtime configuration with thread-safe access
class AppConfig
{
public:
  static AppConfig &get();

  void loadDefaultsFromMacros();

  // getters (copy out for thread safety)
  String getDeviceLocation();
  String getWifiSSID();
  String getWifiPassword();
  String getServerHost();
  String getServerPath();
  String getApiKey();
  uint16_t getServerPort();
  bool getUseTls();
  bool getHttpsInsecure();
  uint32_t getPostIntervalSeconds();
  bool getAlignPostsToMinute();

  // setters (update one or more fields)
  void setDeviceLocation(const String &v);
  void setWifiSSID(const String &v);
  void setWifiPassword(const String &v);
  void setServerHost(const String &v);
  void setServerPath(const String &v);
  void setApiKey(const String &v);
  void setServerPort(uint16_t p);
  void setUseTls(bool b);
  void setHttpsInsecure(bool b);
  void setPostIntervalSeconds(uint32_t s);
  void setAlignPostsToMinute(bool b);

  // JSON helpers (ArduinoJson Document)
  template <typename TDoc>
  void toJson(TDoc &doc)
  {
    xSemaphoreTake(mutex_, portMAX_DELAY);
    doc["device_location"] = deviceLocation_;
    doc["wifi_ssid"] = wifiSSID_;
    doc["wifi_password"] = wifiPassword_;
    doc["server_host"] = serverHost_;
    doc["server_path"] = serverPath_;
    doc["server_port"] = serverPort_;
    doc["use_tls"] = useTls_;
    doc["https_insecure"] = httpsInsecure_;
    doc["api_key"] = apiKey_;
    doc["post_interval_sec"] = postIntervalSeconds_;
    doc["align_to_minute"] = alignPostsToMinute_;
    doc["persisted"] = hasPersistedConfig();
    xSemaphoreGive(mutex_);
  }

  template <typename TDoc>
  void updateFromJson(const TDoc &doc)
  {
    xSemaphoreTake(mutex_, portMAX_DELAY);

    // Strings
    if (doc["device_location"].template is<const char *>())
      deviceLocation_ = doc["device_location"].template as<String>();

    if (doc["wifi_ssid"].template is<const char *>())
      wifiSSID_ = doc["wifi_ssid"].template as<String>();

    if (doc["wifi_password"].template is<const char *>())
      wifiPassword_ = doc["wifi_password"].template as<String>();

    if (doc["server_host"].template is<const char *>())
      serverHost_ = doc["server_host"].template as<String>();

    if (doc["server_path"].template is<const char *>())
      serverPath_ = doc["server_path"].template as<String>();

    // Numbers / bools
    if (doc["server_port"].template is<uint16_t>())
      serverPort_ = doc["server_port"].template as<uint16_t>();

    if (doc["use_tls"].template is<bool>())
      useTls_ = doc["use_tls"].template as<bool>();

    if (doc["https_insecure"].template is<bool>())
      httpsInsecure_ = doc["https_insecure"].template as<bool>();

    if (doc["api_key"].template is<const char *>())
      apiKey_ = doc["api_key"].template as<String>();

    if (!doc["post_interval_sec"].isNull())
    {
      uint32_t tmp = doc["post_interval_sec"].template as<uint32_t>();
      if (tmp == 0)
        tmp = 1;
      postIntervalSeconds_ = tmp;
    }

    if (!doc["align_to_minute"].isNull())
    {
      if (doc["align_to_minute"].template is<bool>())
        alignPostsToMinute_ = doc["align_to_minute"].template as<bool>();
      else
        alignPostsToMinute_ = (doc["align_to_minute"].template as<int>() != 0);
    }

    xSemaphoreGive(mutex_);
  }

  // Persistence helpers (NVS)
  bool saveToNvs();
  bool loadFromNvs();
  bool hasPersistedConfig();
  bool factoryReset();

private:
  AppConfig();

  void loadDefaultsLocked();
  bool loadFromNvsLocked();

  SemaphoreHandle_t mutex_;

  Preferences prefs_;
  bool prefsReady_;

  String deviceLocation_;
  String wifiSSID_;
  String wifiPassword_;
  String serverHost_;
  String serverPath_;
  String apiKey_;
  uint16_t serverPort_;
  bool useTls_;
  bool httpsInsecure_;
  uint32_t postIntervalSeconds_;
  bool alignPostsToMinute_;
};
