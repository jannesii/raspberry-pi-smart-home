#include <Arduino.h>
#include <WiFi.h>
#include <time.h>

// ---- WiFi config ----
const char* ssid     = "REDACTED_WIFI_SSID";
const char* password = "REDACTED_WIFI_PASSWORD";

// ---- Upstream HTTP server to send readings to ----
const char* server_host = "192.168.10.50";  // <-- change to your server/IP or domain
const uint16_t server_port = 8080;          // <-- change to your server port
const char* server_path = "/temperature";   // <-- change to your endpoint path

WiFiServer server(80);

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

#define DHTPIN 10
#define DHTTYPE DHT22

DHT_Unified dht(DHTPIN, DHTTYPE);
// NOTE: We will sync to the clock: boot -> one measurement -> then every minute at :00

// ---- helper: send JSON via HTTP POST ----
bool postReading(float temperatureC, float humidityPct) {
  if (WiFi.status() != WL_CONNECTED) return false;

  WiFiClient client;
  if (!client.connect(server_host, server_port)) {
    Serial.println(F("HTTP connect failed"));
    return false;
  }

  const char* location = "Makuuhuone";

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
    Serial.println(F("Sensor read failed"));
    return false;
  }

  Serial.print(F("Temperature: ")); Serial.print(t, 2); Serial.println(F(" °C"));
  Serial.print(F("Humidity: "));    Serial.print(h, 2); Serial.println(F(" %"));

  bool ok = postReading(t, h);
  if (!ok) {
    Serial.println(F("POST failed"));
  }
  return ok;
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
    while (client.connected()) {
      if (client.available()) {
        char c = client.read();
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
