#include "AppConfig.h"

#include "config.h"

AppConfig& AppConfig::get() {
  static AppConfig inst;
  return inst;
}

AppConfig::AppConfig() {
  mutex_ = xSemaphoreCreateMutex();
  loadDefaultsFromMacros();
}

void AppConfig::loadDefaultsFromMacros() {
  xSemaphoreTake(mutex_, portMAX_DELAY);
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
  xSemaphoreGive(mutex_);
}

String AppConfig::getDeviceLocation() { xSemaphoreTake(mutex_, portMAX_DELAY); String v = deviceLocation_; xSemaphoreGive(mutex_); return v; }
String AppConfig::getWifiSSID() { xSemaphoreTake(mutex_, portMAX_DELAY); String v = wifiSSID_; xSemaphoreGive(mutex_); return v; }
String AppConfig::getWifiPassword() { xSemaphoreTake(mutex_, portMAX_DELAY); String v = wifiPassword_; xSemaphoreGive(mutex_); return v; }
String AppConfig::getServerHost() { xSemaphoreTake(mutex_, portMAX_DELAY); String v = serverHost_; xSemaphoreGive(mutex_); return v; }
String AppConfig::getServerPath() { xSemaphoreTake(mutex_, portMAX_DELAY); String v = serverPath_; xSemaphoreGive(mutex_); return v; }
String AppConfig::getApiKey() { xSemaphoreTake(mutex_, portMAX_DELAY); String v = apiKey_; xSemaphoreGive(mutex_); return v; }
uint16_t AppConfig::getServerPort() { xSemaphoreTake(mutex_, portMAX_DELAY); auto v = serverPort_; xSemaphoreGive(mutex_); return v; }
bool AppConfig::getUseTls() { xSemaphoreTake(mutex_, portMAX_DELAY); auto v = useTls_; xSemaphoreGive(mutex_); return v; }
bool AppConfig::getHttpsInsecure() { xSemaphoreTake(mutex_, portMAX_DELAY); auto v = httpsInsecure_; xSemaphoreGive(mutex_); return v; }

void AppConfig::setDeviceLocation(const String& v) { xSemaphoreTake(mutex_, portMAX_DELAY); deviceLocation_ = v; xSemaphoreGive(mutex_); }
void AppConfig::setWifiSSID(const String& v) { xSemaphoreTake(mutex_, portMAX_DELAY); wifiSSID_ = v; xSemaphoreGive(mutex_); }
void AppConfig::setWifiPassword(const String& v) { xSemaphoreTake(mutex_, portMAX_DELAY); wifiPassword_ = v; xSemaphoreGive(mutex_); }
void AppConfig::setServerHost(const String& v) { xSemaphoreTake(mutex_, portMAX_DELAY); serverHost_ = v; xSemaphoreGive(mutex_); }
void AppConfig::setServerPath(const String& v) { xSemaphoreTake(mutex_, portMAX_DELAY); serverPath_ = v; xSemaphoreGive(mutex_); }
void AppConfig::setApiKey(const String& v) { xSemaphoreTake(mutex_, portMAX_DELAY); apiKey_ = v; xSemaphoreGive(mutex_); }
void AppConfig::setServerPort(uint16_t p) { xSemaphoreTake(mutex_, portMAX_DELAY); serverPort_ = p; xSemaphoreGive(mutex_); }
void AppConfig::setUseTls(bool b) { xSemaphoreTake(mutex_, portMAX_DELAY); useTls_ = b; xSemaphoreGive(mutex_); }
void AppConfig::setHttpsInsecure(bool b) { xSemaphoreTake(mutex_, portMAX_DELAY); httpsInsecure_ = b; xSemaphoreGive(mutex_); }

