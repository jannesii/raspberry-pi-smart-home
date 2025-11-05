#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

// Central runtime configuration with thread-safe access
class AppConfig {
public:
  static AppConfig& get();

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

  // setters (update one or more fields)
  void setDeviceLocation(const String& v);
  void setWifiSSID(const String& v);
  void setWifiPassword(const String& v);
  void setServerHost(const String& v);
  void setServerPath(const String& v);
  void setApiKey(const String& v);
  void setServerPort(uint16_t p);
  void setUseTls(bool b);
  void setHttpsInsecure(bool b);

  // JSON helpers (ArduinoJson Document)
  template <typename TDoc>
  void toJson(TDoc& doc) {
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
    xSemaphoreGive(mutex_);
  }

  template <typename TDoc>
  void updateFromJson(const TDoc& doc) {
    xSemaphoreTake(mutex_, portMAX_DELAY);
    if (doc.containsKey("device_location")) deviceLocation_ = (const char*)doc["device_location"];
    if (doc.containsKey("wifi_ssid")) wifiSSID_ = (const char*)doc["wifi_ssid"];
    if (doc.containsKey("wifi_password")) wifiPassword_ = (const char*)doc["wifi_password"];
    if (doc.containsKey("server_host")) serverHost_ = (const char*)doc["server_host"];
    if (doc.containsKey("server_path")) serverPath_ = (const char*)doc["server_path"];
    if (doc.containsKey("server_port")) serverPort_ = (uint16_t)doc["server_port"].template as<uint16_t>();
    if (doc.containsKey("use_tls")) useTls_ = doc["use_tls"].template as<bool>();
    if (doc.containsKey("https_insecure")) httpsInsecure_ = doc["https_insecure"].template as<bool>();
    if (doc.containsKey("api_key")) apiKey_ = (const char*)doc["api_key"];
    xSemaphoreGive(mutex_);
  }

private:
  AppConfig();

  SemaphoreHandle_t mutex_;

  String deviceLocation_;
  String wifiSSID_;
  String wifiPassword_;
  String serverHost_;
  String serverPath_;
  String apiKey_;
  uint16_t serverPort_;
  bool useTls_;
  bool httpsInsecure_;
};
