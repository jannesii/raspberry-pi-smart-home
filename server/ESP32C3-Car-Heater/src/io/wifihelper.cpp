#include <Arduino.h>
#include <WiFi.h>
#include "io/wifihelper.h"

bool wifiIsConnected() {
    return WiFi.status() == WL_CONNECTED;
}


bool connectWifi(
    String wifiSSID,
    String wifiPassword,
    const IPAddress& wifiStaticIp,
    const IPAddress& wifiGateway,
    const IPAddress& wifiSubnet,
    const IPAddress& wifiDnsPrimary
) {
    Serial.println(F("[WiFi] Setting up (static IP)..."));

    // Configure static IP (do this before WiFi.begin)
    if (!WiFi.config(wifiStaticIp, wifiGateway, wifiSubnet, wifiDnsPrimary)) {
        Serial.println(F("[WiFi] WiFi.config failed (static IP)"));
    }

    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.persistent(false);  // don't write creds to flash every time

    Serial.print(F("[WiFi] Connecting to SSID: "));
    Serial.println(wifiSSID);

    WiFi.begin(wifiSSID.c_str(), wifiPassword.c_str());

    uint8_t retries = 0;
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
        retries++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println();
        Serial.println(F("[WiFi] Connected!"));
        Serial.print(F("[WiFi] IP: "));
        Serial.println(WiFi.localIP());
        return true;
    } else {
        Serial.println();
        Serial.println(F("[WiFi] Failed to connect."));
        return false;
    }
}