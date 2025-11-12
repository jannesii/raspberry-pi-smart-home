#include "AppConfig.h"

#include <esp_err.h>

#include "config.h"

namespace
{
  constexpr const char kPrefsNamespace[] = "appcfg";
  constexpr const char kKeyDeviceLocation[] = "device_location";
  constexpr const char kKeyWifiSsid[] = "wifi_ssid";
  constexpr const char kKeyWifiPassword[] = "wifi_password";
  constexpr const char kKeyServerHost[] = "server_host";
  constexpr const char kKeyServerPath[] = "server_path";
  constexpr const char kKeyApiKey[] = "api_key";
  constexpr const char kKeyServerPort[] = "server_port";
  constexpr const char kKeyUseTls[] = "use_tls";
  constexpr const char kKeyHttpsInsecure[] = "https_insecure";
}

AppConfig &AppConfig::get()
{
  static AppConfig inst;
  return inst;
}

AppConfig::AppConfig() : prefsReady_(false)
{
  mutex_ = xSemaphoreCreateMutex();
  prefsReady_ = prefs_.begin(kPrefsNamespace, false);
  loadDefaultsFromMacros();
  if (prefsReady_)
  {
    (void)loadFromNvs();
  }
}

void AppConfig::loadDefaultsFromMacros()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  loadDefaultsLocked();
  xSemaphoreGive(mutex_);
}

void AppConfig::loadDefaultsLocked()
{
  deviceLocation_ = DEVICE_LOCATION;
  wifiSSID_ = WIFI_SSID;
  wifiPassword_ = WIFI_PASSWORD;
  serverHost_ = HTTP_SERVER_HOST;
  serverPath_ = HTTP_SERVER_PATH;
  apiKey_ = API_KEY;
  serverPort_ = HTTP_SERVER_PORT;
#ifdef HTTP_USE_TLS
  useTls_ = (HTTP_USE_TLS != 0);
#else
  useTls_ = true;
#endif
#ifdef HTTPS_INSECURE
  httpsInsecure_ = (HTTPS_INSECURE != 0);
#else
  httpsInsecure_ = false;
#endif
}

String AppConfig::getDeviceLocation()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  String v = deviceLocation_;
  xSemaphoreGive(mutex_);
  return v;
}
String AppConfig::getWifiSSID()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  String v = wifiSSID_;
  xSemaphoreGive(mutex_);
  return v;
}
String AppConfig::getWifiPassword()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  String v = wifiPassword_;
  xSemaphoreGive(mutex_);
  return v;
}
String AppConfig::getServerHost()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  String v = serverHost_;
  xSemaphoreGive(mutex_);
  return v;
}
String AppConfig::getServerPath()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  String v = serverPath_;
  xSemaphoreGive(mutex_);
  return v;
}
String AppConfig::getApiKey()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  String v = apiKey_;
  xSemaphoreGive(mutex_);
  return v;
}
uint16_t AppConfig::getServerPort()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  auto v = serverPort_;
  xSemaphoreGive(mutex_);
  return v;
}
bool AppConfig::getUseTls()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  auto v = useTls_;
  xSemaphoreGive(mutex_);
  return v;
}
bool AppConfig::getHttpsInsecure()
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  auto v = httpsInsecure_;
  xSemaphoreGive(mutex_);
  return v;
}

void AppConfig::setDeviceLocation(const String &v)
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  deviceLocation_ = v;
  xSemaphoreGive(mutex_);
}
void AppConfig::setWifiSSID(const String &v)
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  wifiSSID_ = v;
  xSemaphoreGive(mutex_);
}
void AppConfig::setWifiPassword(const String &v)
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  wifiPassword_ = v;
  xSemaphoreGive(mutex_);
}
void AppConfig::setServerHost(const String &v)
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  serverHost_ = v;
  xSemaphoreGive(mutex_);
}
void AppConfig::setServerPath(const String &v)
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  serverPath_ = v;
  xSemaphoreGive(mutex_);
}
void AppConfig::setApiKey(const String &v)
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  apiKey_ = v;
  xSemaphoreGive(mutex_);
}
void AppConfig::setServerPort(uint16_t p)
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  serverPort_ = p;
  xSemaphoreGive(mutex_);
}
void AppConfig::setUseTls(bool b)
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  useTls_ = b;
  xSemaphoreGive(mutex_);
}
void AppConfig::setHttpsInsecure(bool b)
{
  xSemaphoreTake(mutex_, portMAX_DELAY);
  httpsInsecure_ = b;
  xSemaphoreGive(mutex_);
}

bool AppConfig::loadFromNvsLocked()
{
  if (!prefsReady_)
    return false;
  bool loaded = false;

  if (prefs_.isKey(kKeyDeviceLocation))
  {
    deviceLocation_ = prefs_.getString(kKeyDeviceLocation, deviceLocation_);
    loaded = true;
  }
  if (prefs_.isKey(kKeyWifiSsid))
  {
    wifiSSID_ = prefs_.getString(kKeyWifiSsid, wifiSSID_);
    loaded = true;
  }
  if (prefs_.isKey(kKeyWifiPassword))
  {
    wifiPassword_ = prefs_.getString(kKeyWifiPassword, wifiPassword_);
    loaded = true;
  }
  if (prefs_.isKey(kKeyServerHost))
  {
    serverHost_ = prefs_.getString(kKeyServerHost, serverHost_);
    loaded = true;
  }
  if (prefs_.isKey(kKeyServerPath))
  {
    serverPath_ = prefs_.getString(kKeyServerPath, serverPath_);
    loaded = true;
  }
  if (prefs_.isKey(kKeyApiKey))
  {
    apiKey_ = prefs_.getString(kKeyApiKey, apiKey_);
    loaded = true;
  }
  if (prefs_.isKey(kKeyServerPort))
  {
    serverPort_ = prefs_.getUShort(kKeyServerPort, serverPort_);
    loaded = true;
  }
  if (prefs_.isKey(kKeyUseTls))
  {
    useTls_ = prefs_.getBool(kKeyUseTls, useTls_);
    loaded = true;
  }
  if (prefs_.isKey(kKeyHttpsInsecure))
  {
    httpsInsecure_ = prefs_.getBool(kKeyHttpsInsecure, httpsInsecure_);
    loaded = true;
  }

  return loaded;
}

bool AppConfig::loadFromNvs()
{
  if (!prefsReady_)
    return false;
  xSemaphoreTake(mutex_, portMAX_DELAY);
  bool loaded = loadFromNvsLocked();
  xSemaphoreGive(mutex_);
  return loaded;
}

bool AppConfig::saveToNvs()
{
  if (!prefsReady_)
    return false;

  // Snapshot values under lock to keep writes consistent.
  String deviceLocation;
  String wifiSsid;
  String wifiPassword;
  String serverHost;
  String serverPath;
  String apiKey;
  uint16_t serverPort;
  bool useTls;
  bool httpsInsecure;

  xSemaphoreTake(mutex_, portMAX_DELAY);
  deviceLocation = deviceLocation_;
  wifiSsid = wifiSSID_;
  wifiPassword = wifiPassword_;
  serverHost = serverHost_;
  serverPath = serverPath_;
  apiKey = apiKey_;
  serverPort = serverPort_;
  useTls = useTls_;
  httpsInsecure = httpsInsecure_;
  xSemaphoreGive(mutex_);

  prefs_.putString(kKeyDeviceLocation, deviceLocation);
  prefs_.putString(kKeyWifiSsid, wifiSsid);
  prefs_.putString(kKeyWifiPassword, wifiPassword);
  prefs_.putString(kKeyServerHost, serverHost);
  prefs_.putString(kKeyServerPath, serverPath);
  prefs_.putString(kKeyApiKey, apiKey);
  prefs_.putUShort(kKeyServerPort, serverPort);
  prefs_.putBool(kKeyUseTls, useTls);
  prefs_.putBool(kKeyHttpsInsecure, httpsInsecure);

  return true;
}

bool AppConfig::hasPersistedConfig()
{
  if (!prefsReady_)
    return false;
  return prefs_.isKey(kKeyDeviceLocation) ||
         prefs_.isKey(kKeyWifiSsid) ||
         prefs_.isKey(kKeyWifiPassword) ||
         prefs_.isKey(kKeyServerHost) ||
         prefs_.isKey(kKeyServerPath) ||
         prefs_.isKey(kKeyApiKey) ||
         prefs_.isKey(kKeyServerPort) ||
         prefs_.isKey(kKeyUseTls) ||
         prefs_.isKey(kKeyHttpsInsecure);
}

bool AppConfig::factoryReset()
{
  if (!prefsReady_)
  {
    loadDefaultsFromMacros();
    return true;
  }
  esp_err_t err = prefs_.clear();
  loadDefaultsFromMacros();
  return (err == ESP_OK);
}
