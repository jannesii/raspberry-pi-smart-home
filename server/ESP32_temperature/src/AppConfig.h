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
  String getWifiHostname();
  String getMdnsHostname();
  String getServerHost();
  String getServerPath();
  String getApiKey();
  String getHttpApiKey();
  uint16_t getServerPort();
  bool getUseTls();
  bool getHttpsInsecure();
  uint32_t getPostIntervalSeconds();
  bool getAlignPostsToMinute();
  bool getWifiStaticIpEnabled();
  String getWifiStaticIp();
  String getWifiStaticGateway();
  String getWifiStaticSubnet();
  String getWifiStaticDns1();
  String getWifiStaticDns2();

  // setters (update one or more fields)
  void setDeviceLocation(const String &v);
  void setWifiSSID(const String &v);
  void setWifiPassword(const String &v);
  void setWifiHostname(const String &v);
  void setMdnsHostname(const String &v);
  void setServerHost(const String &v);
  void setServerPath(const String &v);
  void setApiKey(const String &v);
  void setHttpApiKey(const String &v);
  void setServerPort(uint16_t p);
  void setUseTls(bool b);
  void setHttpsInsecure(bool b);
  void setPostIntervalSeconds(uint32_t s);
  void setAlignPostsToMinute(bool b);
  void setWifiStaticIpEnabled(bool b);
  void setWifiStaticIp(const String &v);
  void setWifiStaticGateway(const String &v);
  void setWifiStaticSubnet(const String &v);
  void setWifiStaticDns1(const String &v);
  void setWifiStaticDns2(const String &v);

  // JSON helpers (ArduinoJson Document)
  template <typename TDoc>
  void toJson(TDoc &doc)
  {
    xSemaphoreTake(mutex_, portMAX_DELAY);
    doc["device_location"] = deviceLocation_;
    doc["wifi_ssid"] = wifiSSID_;
    doc["wifi_password"] = wifiPassword_;
    doc["wifi_hostname"] = wifiHostname_;
    doc["mdns_hostname"] = mdnsHostname_;
    doc["server_host"] = serverHost_;
    doc["server_path"] = serverPath_;
    doc["server_port"] = serverPort_;
    doc["use_tls"] = useTls_;
    doc["https_insecure"] = httpsInsecure_;
    doc["api_key"] = apiKey_;
    doc["http_api_key"] = httpApiKey_;
    doc["post_interval_sec"] = postIntervalSeconds_;
    doc["align_to_minute"] = alignPostsToMinute_;
    doc["wifi_static_ip_enabled"] = wifiStaticIpEnabled_;
    doc["wifi_static_ip"] = wifiStaticIp_;
    doc["wifi_static_gateway"] = wifiStaticGateway_;
    doc["wifi_static_netmask"] = wifiStaticSubnet_;
    doc["wifi_static_dns1"] = wifiStaticDns1_;
    doc["wifi_static_dns2"] = wifiStaticDns2_;
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

    if (doc["wifi_hostname"].template is<const char *>())
      wifiHostname_ = doc["wifi_hostname"].template as<String>();

    if (doc["mdns_hostname"].template is<const char *>())
      mdnsHostname_ = doc["mdns_hostname"].template as<String>();

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

    if (doc["http_api_key"].template is<const char *>())
      httpApiKey_ = doc["http_api_key"].template as<String>();

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

    if (!doc["wifi_static_ip_enabled"].isNull())
    {
      if (doc["wifi_static_ip_enabled"].template is<bool>())
        wifiStaticIpEnabled_ = doc["wifi_static_ip_enabled"].template as<bool>();
      else
        wifiStaticIpEnabled_ = (doc["wifi_static_ip_enabled"].template as<int>() != 0);
    }

    if (doc["wifi_static_ip"].template is<const char *>())
      wifiStaticIp_ = doc["wifi_static_ip"].template as<String>();

    if (doc["wifi_static_gateway"].template is<const char *>())
      wifiStaticGateway_ = doc["wifi_static_gateway"].template as<String>();

    if (doc["wifi_static_netmask"].template is<const char *>())
      wifiStaticSubnet_ = doc["wifi_static_netmask"].template as<String>();

    if (doc["wifi_static_dns1"].template is<const char *>())
      wifiStaticDns1_ = doc["wifi_static_dns1"].template as<String>();

    if (doc["wifi_static_dns2"].template is<const char *>())
      wifiStaticDns2_ = doc["wifi_static_dns2"].template as<String>();

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
  String wifiHostname_;
  String mdnsHostname_;
  String serverHost_;
  String serverPath_;
  String apiKey_;
  String httpApiKey_;
  uint16_t serverPort_;
  bool useTls_;
  bool httpsInsecure_;
  uint32_t postIntervalSeconds_;
  bool alignPostsToMinute_;
  bool wifiStaticIpEnabled_;
  String wifiStaticIp_;
  String wifiStaticGateway_;
  String wifiStaticSubnet_;
  String wifiStaticDns1_;
  String wifiStaticDns2_;
};
