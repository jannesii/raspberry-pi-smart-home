#include <Arduino.h>
#include <ESPmDNS.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <LittleFS.h>

#include "wifihelper.h"
#include "WebInterface.h"
#include "ShellyHandler.h"
#include <measurements.h>
#include "HeaterTask.h"
#include "Thermostat.h"
#include "Config.h"
#include "staticconfig.h"
#include "LogManager.h"
#include "TimeKeeper.h"
#include "WatchDog.h"
#include "LedManager.h"
#include "WebSocketHub.h"
#include "ReadyByTask.h"

#include <nvs_flash.h>
#include <nvs.h>

bool initMDNS();
void printNvsStats();

static AsyncWebServer server(80);

static Config config;
static Thermostat thermostat(0.0f, 0.0f); // will overwrite below
static ShellyHandler shelly(SHELLY_IP);
static LogManager logManager;
static LedManager ledManager(LED_PIN, LED_ACTIVE_HIGH != 0);
static HeaterTask heaterTask(config, thermostat, shelly, logManager, ledManager);
static WatchDog watchdog(config, thermostat, shelly, logManager, ledManager, heaterTask);
static ReadyByTask readyByTask(config, heaterTask, logManager);

static WebSocketHub webSocketHub(server, heaterTask, readyByTask, config);
static WebInterface webInterface(
    server,
    config,
    thermostat,
    shelly,
    logManager,
    WIFI_SSID,
    ledManager,
    heaterTask,
    readyByTask);

void setup()
{
    Serial.begin(115200);
    delay(2000);
    Serial.println("Booting...");
    connectWifi(
        WIFI_SSID,
        WIFI_PASSWORD,
        IPAddress(WIFI_STATIC_IP_OCTETS),
        IPAddress(WIFI_GATEWAY_OCTETS),
        IPAddress(WIFI_SUBNET_OCTETS),
        IPAddress(WIFI_DNS_PRIMARY_OCTETS));
    if (!LittleFS.begin())
        Serial.println("Failed to mount FS");
    else
        Serial.println("File system mounted");

    if (!config.begin())
        Serial.println("⚠️ [Config] Failed to init NVS");
    else
        Serial.println("[Config] Config loaded");

    // Initialize timekeeper (timezone offset is loaded; clock starts invalid until synced)
    if (!timekeeper::begin())
        Serial.println("⚠️ [Timekeeper] Failed to initialize; time features limited.");
    else
        Serial.println("[Timekeeper] Initialized");

    if (!logManager.begin())
        Serial.println("⚠️ [LogManager] Failed to initialize");
    else
        Serial.println("[LogManager] Initialized");

    thermostat.setTarget(config.targetTemp());
    thermostat.setHysteresis(config.hysteresis());

    initMDNS();

    initBMP280(
        BMP280_I2C_ADDRESS,
        I2C_SDA_PIN,
        I2C_SCL_PIN);

    // Start LED manager
    ledManager.begin();

    watchdog.begin(4096, 2); // stack size, priority
    heaterTask.setKickCallback([]()
                               { watchdog.kickHeater(); });

    heaterTask.start(4096, 1); // stack size, priority

    webInterface.begin();

    server.begin();
    Serial.println("[HTTP] Async WebServer started on port 80");

    // Setup WebSocket integration
    webSocketHub.begin();
    heaterTask.setWsTempUpdateCallback([]()
                            { webSocketHub.broadcastTempUpdate(); });
    logManager.setCallback([](const String &line)
                            { webSocketHub.broadcastLogLine(line); });
    readyByTask.setWsReadyByUpdateCallback([]()
                            { webSocketHub.broadcastReadyByUpdate(); });

    // Print NVS stats
    printNvsStats();

    // Ready indicator: five slow blinks, 1s apart
    for (int i = 0; i < 5; ++i)
    {
        ledManager.blinkSingle();
        delay(1000);
    }
    // logManager.dumpToSerial();
}

void loop() {}

bool initMDNS()
{
    if (MDNS.begin("car-heater"))
    {
        MDNS.addService("http", "tcp", 80);
        Serial.println("[mDNS] Started: http://car-heater.local/");
        return true;
    }
    else
    {
        Serial.println("⚠️ [mDNS] Failed to start mDNS responder");
        return false;
    }
}

void printNvsStats()
{
    nvs_stats_t stats;
    esp_err_t err = nvs_get_stats(NULL, &stats); // NULL = default "nvs" partition
    if (err != ESP_OK)
    {
        Serial.printf("⚠️ [NVS] nvs_get_stats failed: %d\n", (int)err);
        return;
    }

    Serial.println("[NVS] Stats for default NVS partition:");
    Serial.printf("  Used entries:  %u\n", stats.used_entries);
    Serial.printf("  Free entries:  %u\n", stats.free_entries);
    Serial.printf("  All entries:   %u\n", stats.total_entries);
    Serial.printf("  Namespace cnt: %u\n", stats.namespace_count);
}
