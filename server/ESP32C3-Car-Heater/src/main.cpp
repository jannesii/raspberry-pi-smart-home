#include <Arduino.h>
#include <ESPmDNS.h>

#include "io/wifihelper.h"
#include "io/ShellyHandler.h"
#include "io/measurements.h"
#include "core/staticconfig.h"
#include "core/TimeKeeper.h"
#include "heating/PosterTask.h"

#include <nvs_flash.h>
#include <nvs.h>

static ShellyHandler shelly(SHELLY_IP);
static LogManager logManager;
static PosterTask posterTask(shelly, logManager);

bool initMDNS();
void printNvsStats();

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

    // Initialize timekeeper (timezone offset is loaded; clock starts invalid until synced)
    if (!timekeeper::begin())
        Serial.println("⚠️ [Timekeeper] Failed to initialize; time features limited.");
    else
        Serial.println("[Timekeeper] Initialized");;

    if (!logManager.begin())
        Serial.println("⚠️ [LogManager] Failed to initialize log manager.");

    initMDNS();

    initBMP280(
        BMP280_I2C_ADDRESS,
        I2C_SDA_PIN,
        I2C_SCL_PIN);

    posterTask.start(8192, 1); // stack size, priority

    // Print NVS stats
    printNvsStats();

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
