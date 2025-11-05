#include "Poster.h"

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <time.h>

#include "config.h"
#include "AppConfig.h"

static inline void logHeap(const char* tag) {
  Serial.printf("[Heap][%s] Free:%u Min:%u\n", tag, ESP.getFreeHeap(), ESP.getMinFreeHeap());
}

Poster::Poster() {}

bool Poster::postJSON(const String &body) {
  if (WiFi.status() != WL_CONNECTED) return false;

  auto &cfg = AppConfig::get();
  const String host = cfg.getServerHost();
  const uint16_t port = cfg.getServerPort();
  const String path = cfg.getServerPath();
  const String apiKey = cfg.getApiKey();
  const bool useTls = cfg.getUseTls();
  const bool insecure = cfg.getHttpsInsecure();

  auto sendRequest = [&](Client &c) {
    c.print(F("POST "));
    c.print(path);
    c.println(F(" HTTP/1.1"));
    c.print(F("Host: "));
    c.print(host);
    if (port != 80 && port != 443) {
      c.print(F(":"));
      c.print(port);
    }
    c.println();
    if (apiKey.length()) {
      c.print(F("Authorization: Bearer "));
      c.println(apiKey);
    }
    c.println(F("Content-Type: application/json"));
    c.print(F("Content-Length: "));
    c.println(body.length());
    c.println(F("Connection: close"));
    c.println();
    c.print(body);
  };

  if (useTls) {
    WiFiClientSecure client;
    if (insecure) {
      client.setInsecure();
    } else {
      client.setCACert(kHttpsRootCA);
    }
    if (!client.connect(host.c_str(), port)) {
      Serial.println(F("HTTP connect failed (TLS)"));
      return false;
    }
    sendRequest(client);
    unsigned long start = millis();
    while (!client.available() && millis() - start < 1500) delay(10);
    if (client.available()) {
      String statusLine = client.readStringUntil('\n');
      statusLine.trim();
      Serial.print(F("HTTP status: ")); Serial.println(statusLine);
    }
    client.stop();
  } else {
    WiFiClient client;
    if (!client.connect(host.c_str(), port)) {
      Serial.println(F("HTTP connect failed"));
      return false;
    }
    sendRequest(client);
    unsigned long start = millis();
    while (!client.available() && millis() - start < 1500) delay(10);
    if (client.available()) {
      String statusLine = client.readStringUntil('\n');
      statusLine.trim();
      Serial.print(F("HTTP status: ")); Serial.println(statusLine);
    }
    client.stop();
  }

  logHeap("postJSON");
  return true;
}

bool Poster::postError(const String &message) {
  String body;
  body.reserve(128);
  body += F("{\"location\":\"");
  body += AppConfig::get().getDeviceLocation();
  body += F("\",\"error\":\"");
  body += message;
  body += F("\"}");

  return postJSON(body);
}

bool Poster::postReading(float temperatureC, float humidityPct) {
  String body;
  body.reserve(64);
  body += F("{\"location\":\"");
  body += AppConfig::get().getDeviceLocation();
  body += F("\",\"temperature_c\":");
  body += String(temperatureC, 2);
  body += F(",\"humidity_pct\":");
  body += String(humidityPct, 2);
  body += F("}");

  return postJSON(body);
}
