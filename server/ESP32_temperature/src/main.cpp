#include <Arduino.h>
#include <WiFi.h>

// ---- WiFi config ----
const char* ssid     = "REDACTED_WIFI_SSID";
const char* password = "REDACTED_WIFI_PASSWORD";

// ---- Upstream HTTP server to send readings to ----
const char* server_host = "192.168.10.50";  // <-- change to your server/IP or domain
const uint16_t server_port = 8080;          // <-- change to your server port
const char* server_path = "/temperature";   // <-- change to your endpoint path

WiFiServer server(80);

// ---- DHT config ----
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <DHT_U.h>

#define DHTPIN 10
#define DHTTYPE DHT22

DHT_Unified dht(DHTPIN, DHTTYPE);
uint32_t delayMS = 60000;   // will be adjusted from sensor.min_delay if desired

// Post immediately once after boot, then every delayMS
bool firstPostSent = false;
unsigned long lastDHT = 0;

// ---- helper: send JSON via HTTP POST ----
bool postReading(float temperatureC, float humidityPct) {
  if (WiFi.status() != WL_CONNECTED) return false;

  WiFiClient client;
  if (!client.connect(server_host, server_port)) {
    Serial.println(F("HTTP connect failed"));
    return false;
  }

  const char* location = "Tietokonepöytä";

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

  // DHT
  dht.begin();
  Serial.println(F("DHT sensor initialized"));

  sensor_t sensor;
  dht.temperature().getSensor(&sensor);
  /* If you want to honor sensor's min_delay:
  if (sensor.min_delay > 0) {
    delayMS = max<uint32_t>(1000, sensor.min_delay / 1000); // min_delay in µs
  } */
  Serial.print(F("DHT interval: "));
  Serial.print(delayMS);
  Serial.println(F(" ms"));

  // We will post immediately in loop() by leaving firstPostSent=false.
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

  // --- Read DHT sensor and POST to server ---
  // Post immediately once after boot (firstPostSent == false), then every delayMS
  if (!firstPostSent || (millis() - lastDHT >= delayMS)) {
    sensors_event_t event;
    float t = NAN, h = NAN;

    dht.temperature().getEvent(&event);
    if (!isnan(event.temperature)) t = event.temperature;

    dht.humidity().getEvent(&event);
    if (!isnan(event.relative_humidity)) h = event.relative_humidity;

    if (isnan(t) || isnan(h)) {
      Serial.println(F("Sensor read failed"));
      // Don't mark firstPostSent so we retry soon
      delay(250);
      return;
    }

    Serial.print(F("Temperature: ")); Serial.print(t, 2); Serial.println(F(" °C"));
    Serial.print(F("Humidity: "));    Serial.print(h, 2); Serial.println(F(" %"));

    bool ok = postReading(t, h);
    if (ok) {
      firstPostSent = true;           // we have posted at least once
      lastDHT = millis();             // start the interval after a post
    } else {
      Serial.println(F("POST failed"));
      // Keep firstPostSent as-is so we retry quickly if it's the first post
      delay(500);
    }
  }
}
