#include <Arduino.h>
#include <WiFi.h>
#include <time.h>
#include "config.h"

// ---- WiFi config (moved to config) ----
const char* ssid     = WIFI_SSID;
const char* password = WIFI_PASSWORD;

// ---- Upstream HTTP server to send readings to (from config) ----
const char* server_host = HTTP_SERVER_HOST;  // server/IP or domain
const uint16_t server_port = HTTP_SERVER_PORT;          // server port
const char* server_path = HTTP_SERVER_PATH;   // endpoint path

WiFiServer server(80);

// Logical location of this device (from config)
static const char* kLocation = DEVICE_LOCATION;

// ---- Time/NTP config ----
// Use UTC to align all devices at the same minute boundaries
const char* ntp1 = "pool.ntp.org";
const char* ntp2 = "time.nist.gov";
const char* ntp3 = "time.google.com";
bool timeSynced = false;
time_t nextPostEpoch = 0;           // next UTC epoch second to post (aligned to minute)
unsigned long nextPostMillis = 0;   // fallback if no time sync yet

// ---- DHT config ----
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <DHT_U.h>


DHT_Unified dht(DHTPIN, DHTTYPE);
// NOTE: We will sync to the clock: boot -> one measurement -> then every minute at :00

// Track DHT consecutive failures for gentle self-healing
static uint8_t dhtFailCount = 0;

// ---- helper: heap diagnostics ----
static inline void logHeap(const char* tag) {
  Serial.printf("[Heap][%s] Free:%u Min:%u\n", tag, ESP.getFreeHeap(), ESP.getMinFreeHeap());
}

// ---- helper: low-level HTTP JSON POST ----
static bool postJSON(const String& body) {
  if (WiFi.status() != WL_CONNECTED) return false;

  WiFiClient client;
  if (!client.connect(server_host, server_port)) {
    Serial.println(F("HTTP connect failed"));
    return false;
  }

  // Build and send HTTP/1.1 request
  client.print(F("POST "));
  client.print(server_path);
  client.println(F(" HTTP/1.1"));
  client.print(F("Host: "));
  client.println(server_host);
  client.println(F("Content-Type: application/json"));
  client.print(F("Content-Length: "));
  client.println(body.length());
  client.println(F("Connection: close"));
  client.println();
  client.print(body);

  // Optional: read minimal response (status line) to confirm
  unsigned long start = millis();
  while (!client.available() && millis() - start < 1500) {
    delay(10);
  }
  if (client.available()) {
    String statusLine = client.readStringUntil('\n');
    statusLine.trim();
    Serial.print(F("HTTP status: "));
    Serial.println(statusLine);
  } else {
    Serial.println(F("No HTTP response"));
  }

  client.stop();
  logHeap("postJSON");
  return true;
}

// ---- helper: send error JSON with location ----
bool postError(const char* message) {
  if (WiFi.status() != WL_CONNECTED) return false;

  WiFiClient client;
  if (!client.connect(server_host, server_port)) {
    Serial.println(F("HTTP connect failed"));
    return false;
  }

  // Build JSON body
  String body;
  body.reserve(128);
  body += F("{\"location\":\"");
  body += kLocation;
  body += F("\",\"error\":\"");
  body += message;
  body += F("\"}");

  // Build and send HTTP/1.1 request
  client.print(F("POST "));
  client.print(server_path);
  client.println(F(" HTTP/1.1"));
  client.print(F("Host: "));
  client.println(server_host);
  client.println(F("Content-Type: application/json"));
  client.print(F("Content-Length: "));
  client.println(body.length());
  client.println(F("Connection: close"));
  client.println();
  client.print(body);

  // Optional: read minimal response (status line) to confirm
  unsigned long start = millis();
  while (!client.available() && millis() - start < 1500) {
    delay(10);
  }
  if (client.available()) {
    String statusLine = client.readStringUntil('\n');
    statusLine.trim();
    Serial.print(F("HTTP status: "));
    Serial.println(statusLine);
  } else {
    Serial.println(F("No HTTP response"));
  }

  client.stop();
  logHeap("postError");
  return true;
}

// ---- helper: send JSON via HTTP POST ----
bool postReading(float temperatureC, float humidityPct) {
  if (WiFi.status() != WL_CONNECTED) return false;

  WiFiClient client;
  if (!client.connect(server_host, server_port)) {
    Serial.println(F("HTTP connect failed"));
    return false;
  }

  const char* location = kLocation;

  // Build JSON body
  String body;
  body.reserve(64);
  body += F("{\"location\":\"");
  body += location;
  body += F("\",\"temperature_c\":");
  body += String(temperatureC, 2);
  body += F(",\"humidity_pct\":");
  body += String(humidityPct, 2);
  body += F("}");

  // Build and send HTTP/1.1 request
  client.print(F("POST "));
  client.print(server_path);
  client.println(F(" HTTP/1.1"));
  client.print(F("Host: "));
  client.println(server_host);
  client.println(F("Content-Type: application/json"));
  client.print(F("Content-Length: "));
  client.println(body.length());
  client.println(F("Connection: close"));
  client.println();
  client.print(body);

  // Optional: read minimal response (status line) to confirm
  unsigned long start = millis();
  while (!client.available() && millis() - start < 1500) {
    delay(10);
  }
  if (client.available()) {
    String statusLine = client.readStringUntil('\n');
    statusLine.trim();
    Serial.print(F("HTTP status: "));
    Serial.println(statusLine);
  } else {
    Serial.println(F("No HTTP response"));
  }

  client.stop();
  logHeap("postReading");
  return true;
}

// ---- helper: read DHT and POST once ----
bool readAndPost() {
  sensors_event_t event;
  float t = NAN, h = NAN;

  dht.temperature().getEvent(&event);
  if (!isnan(event.temperature)) t = event.temperature;

  dht.humidity().getEvent(&event);
  if (!isnan(event.relative_humidity)) h = event.relative_humidity;

  if (isnan(t) || isnan(h)) {
    dhtFailCount++;
    String err;
    err.reserve(64);
    err += F("DHT read failed: ");
    if (isnan(t) && isnan(h))      err += F("temp+hum");
    else if (isnan(t))             err += F("temp");
    else if (isnan(h))             err += F("hum");
    Serial.println(err);
    if ((dhtFailCount % 3) == 0) {
      Serial.println(F("Reinitializing DHT sensor..."));
      dht.begin();
    }
    (void)postError(err.c_str());
    return false;
  }

  Serial.print(F("Temperature: ")); Serial.print(t, 2); Serial.println(F(" °C"));
  Serial.print(F("Humidity: "));    Serial.print(h, 2); Serial.println(F(" %"));

  // Successful read resets failure counter
  dhtFailCount = 0;
  return postReading(t, h);
}

// ---- Setup ----
void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println();
  Serial.println(F("Booting..."));

  // WiFi
  Serial.print(F("Connecting to "));
  Serial.println(ssid);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print('.');
  }
  Serial.println();
  Serial.println(F("WiFi connected."));
  Serial.print(F("IP address: "));
  Serial.println(WiFi.localIP());
  server.begin();
  logHeap("after WiFi");

  // NTP time sync (UTC)
  configTime(0 /*gmtOffset*/, 0 /*dstOffset*/, ntp1, ntp2, ntp3);
  Serial.print(F("Syncing time via NTP"));
  struct tm timeinfo;
  for (int i = 0; i < 20; ++i) {          // try for up to ~20 seconds
    if (getLocalTime(&timeinfo, 1000)) {  // wait up to 1s per try
      timeSynced = true;
      break;
    }
    Serial.print('.');
  }
  Serial.println();
  if (timeSynced) {
    Serial.println(F("Time synchronized."));
  } else {
    Serial.println(F("Time sync failed; will retry in background."));
  }

  // DHT
  dht.begin();
  Serial.println(F("DHT sensor initialized"));
  logHeap("after DHT begin");

  sensor_t sensor;
  dht.temperature().getSensor(&sensor);
  (void)sensor; // not used further, but keep call to ensure sensor is reachable

  // Take one immediate measurement after boot
  (void)readAndPost();

  // Schedule next measurement on the next minute boundary (UTC),
  // or fallback to a 60s cadence if time not yet synced.
  if (timeSynced) {
    time_t now = time(nullptr);
    nextPostEpoch = ((now / 60) + 1) * 60;   // next mm:00
    Serial.print(F("Next measurement (epoch): "));
    Serial.println((long)nextPostEpoch);
  } else {
    nextPostMillis = millis() + 60000UL;
  }
}

// ---- Loop ----
void loop() {
  // --- Handle simple web client (Wi-Fi status page) ---
  WiFiClient client = server.accept();
  if (client) {
    Serial.println(F("New Client."));
    String currentLine;
    client.setTimeout(1000);
    unsigned long lastActivity = millis();
    while (client.connected()) {
      if (client.available()) {
        char c = client.read();
        lastActivity = millis();
        if (c == '\n') {
          if (currentLine.length() == 0) {
            client.println(F("HTTP/1.1 200 OK"));
            client.println(F("Content-type:text/html"));
            client.println();
            if (WiFi.status() == WL_CONNECTED) {
              client.println(F("WiFi is <b>CONNECTED</b><br>"));
            } else {
              client.println(F("WiFi is <b>NOT CONNECTED</b><br>"));
            }
            client.println();
            break;
          } else {
            currentLine = "";
          }
        } else if (c != '\r') {
          currentLine += c;
        }
      } else {
        if (millis() - lastActivity > 2000UL) {
          break;
        }
        delay(1);
        yield();
      }
    }
    client.stop();
    Serial.println(F("Client Disconnected."));
  }

  // --- Read DHT sensor and POST on minute boundaries (UTC) ---
  if (timeSynced) {
    time_t now = time(nullptr);
    if (now >= nextPostEpoch) {
      (void)readAndPost();
      nextPostEpoch += 60;  // strictly keep minute alignment
    }
  } else {
    // Fallback cadence until time sync is available
    if (millis() >= nextPostMillis) {
      (void)readAndPost();
      nextPostMillis += 60000UL;
    }

    // Retry time sync occasionally without blocking the loop
    static unsigned long lastSyncTry = 0;
    if (WiFi.status() == WL_CONNECTED && millis() - lastSyncTry > 10000UL) {
      lastSyncTry = millis();
      struct tm ti;
      if (getLocalTime(&ti, 1)) { // quick check
        timeSynced = true;
        time_t now = time(nullptr);
        nextPostEpoch = ((now / 60) + 1) * 60;
        Serial.println(F("Time synchronized (late); switching to minute boundaries."));
      }
    }
  }
}
