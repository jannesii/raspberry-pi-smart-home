#include <Arduino.h>

#include "webtemplates.h"
#include "wifihelper.h"
#include "WebRoutes.h"
#include "measurements.h" // for Measurements
#include "timekeeper.h"   // for time synchronization
#include "Config.h"
#include "Thermostat.h"
#include "ShellyHandler.h"
#include "LogManager.h"
#include "LedManager.h"

static String fmtHHMM(uint16_t minutes);

void setupRoutes(AsyncWebServer &server,
                 Config &config,
                 Thermostat &thermostat,
                 ShellyHandler &shelly,
                 LogManager &logManager,
                 String wifiSSID,
                 LedManager &led)
{
  server.on("/", HTTP_GET, [&shelly, &config, wifiSSID](AsyncWebServerRequest *request)
            {
      Serial.println("[Web] Serving main page");
      // Fetch latest status from Shelly for THIS page load
      bool isOn;
      String heaterState;
      String heaterBtnClass;
      String heaterBtnLabel;
      if (!shelly.getStatus(isOn))
      {
        Serial.println("[Web] Failed to get Shelly status before rendering page");
        heaterState = "Unknown";
        heaterBtnClass = "unknown";
        heaterBtnLabel = "Retry";
      }
      else
      {
        heaterState = isOn ? "ON" : "OFF";
        heaterBtnClass = isOn ? "off" : "on";
        heaterBtnLabel = isOn ? "Turn OFF" : "Turn ON";
      }
      float currentTemp = takeMeasurement().temperature; // Update g_currentTempC
      String currentTime = timekeeper::isValid() ? timekeeper::formatLocal() : "Not set";

      String page = FPSTR(INDEX_HTML);

      page.replace("%TIME_SYNCED%", timekeeper::isValid() ? "1" : "0");
      page.replace("%WIFI_SSID%", wifiSSID);
      page.replace("%TEMP%", String(currentTemp, 1));
      page.replace("%HEATER_STATE%", heaterState);
      page.replace("%HEATER_BTN_CLASS%", heaterBtnClass);
      page.replace("%HEATER_BTN_LABEL%", heaterBtnLabel);
      page.replace("%CURRENT_TIME%", currentTime);

      page.replace("%TARGET_TEMP%", String(config.targetTemp(), 1));
      page.replace("%HYST%", String(config.hysteresis(), 1));
      page.replace("%TASK_DELAY%", String(config.heaterTaskDelayS(), 1));
      page.replace("%DZ_START%", fmtHHMM(config.deadzoneStartMin()));
      page.replace("%DZ_END%", fmtHHMM(config.deadzoneEndMin()));

      request->send(200, "text/html", page); });

  server.on("/toggle", HTTP_POST, [&shelly, &led](AsyncWebServerRequest *request)
            {
    Serial.println("[Web] Toggle heater request received");
    bool ok = shelly.toggle();
    if (ok) {
      led.blinkSingle();
    } else {
      led.blinkTriple();
    }
    request->redirect("/"); });

  server.on("/sync-time", HTTP_POST, [&led](AsyncWebServerRequest *request)
            {
    Serial.println("[Web] Time sync request received");
    if (!request->hasParam("epoch", true) || !request->hasParam("tz", true)) {
      request->send(400, "text/plain", "Missing epoch or tz");
      return;
    }

    auto pEpoch = request->getParam("epoch", true);
    auto pTz    = request->getParam("tz", true);

    uint64_t epoch = strtoull(pEpoch->value().c_str(), nullptr, 10);
    int16_t tzMin  = static_cast<int16_t>(strtol(pTz->value().c_str(), nullptr, 10));

    timekeeper::setUtcWithOffset(epoch, tzMin);
    led.blinkSingle();

    request->redirect("/"); });

  // Endpoint to update config from POST form
  server.on("/set-config", HTTP_POST, [&config, &thermostat, &led](AsyncWebServerRequest *request)
    {
      if (request->hasParam("target", true))
      {
        auto p = request->getParam("target", true);
        float t = p->value().toFloat();
        config.setTargetTemp(t);
        thermostat.setTarget(t);
      }
      if (request->hasParam("hyst", true))
      {
        auto p = request->getParam("hyst", true);
        float h = p->value().toFloat();
        config.setHysteresis(h);
        thermostat.setHysteresis(h);
      }
      if (request->hasParam("taskdelay", true))
      {
        auto p = request->getParam("taskdelay", true);
        float d = p->value().toFloat();
        config.setHeaterTaskDelayS(d);
      }
      if (request->hasParam("dzstart", true))
      {
        auto p = request->getParam("dzstart", true);
        String v = p->value();
        uint16_t h = v.substring(0, 2).toInt();
        uint16_t m = v.substring(3, 5).toInt();
        config.setDeadzoneStartMin(h * 60 + m);
      }
      if (request->hasParam("dzend", true))
      {
        auto p = request->getParam("dzend", true);
        String v = p->value();
        uint16_t h = v.substring(0, 2).toInt();
        uint16_t m = v.substring(3, 5).toInt();
        config.setDeadzoneEndMin(h * 60 + m);
      }

      config.save(); // persist to NVS
      led.blinkSingle();

      request->redirect("/"); });

  server.on("/logs", HTTP_GET, [&logManager](AsyncWebServerRequest* request) {
    String page = FPSTR(LOGS_HTML);
    String logs = logManager.toStringNewestFirst();  // newest→oldest

    if (logs.isEmpty()) {
        logs = "No log entries yet.";
    }

    page.replace("%LOG_LINES%", logs);
    request->send(200, "text/html", page);
  });
  // POST /logs/clear → clear NVS logs and redirect back
  server.on("/logs/clear", HTTP_POST, [&logManager, &led](AsyncWebServerRequest* request) {
      logManager.clear();
      led.blinkSingle();
      request->redirect("/logs");
  });
}

static String fmtHHMM(uint16_t minutes) {
    uint16_t h = (minutes / 60) % 24;
    uint16_t m = minutes % 60;
    char buf[6];
    snprintf(buf, sizeof(buf), "%02u:%02u", h, m);
    return String(buf);
}
