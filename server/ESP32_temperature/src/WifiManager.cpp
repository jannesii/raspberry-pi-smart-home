#include "WifiManager.h"

#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <ctype.h>

#include "AppConfig.h"
#include "Metrics.h"
#include "StructuredLog.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

namespace
{
    constexpr unsigned long kInitialBackoffMs = 500UL;
    constexpr unsigned long kMaxBackoffMs = 60000UL;
    constexpr size_t kMaxHostnameLen = 24; // keep hostnames short for DHCP/mDNS compatibility

    struct StaticIpSettings
    {
        bool enabled = false;
        bool valid = false;
        IPAddress ip;
        IPAddress gateway;
        IPAddress subnet;
        IPAddress dns1;
        IPAddress dns2;
    };

    portMUX_TYPE gReconnectMux = portMUX_INITIALIZER_UNLOCKED;
    volatile bool gReconnectRequested = false;
    volatile bool gImmediateRequested = false;

    unsigned long gNextAttemptMillis = 0;
    unsigned long gCurrentBackoffMs = kInitialBackoffMs;
    uint32_t gAttemptCounter = 0;

    bool gWasConnected = false;
    bool gMdnsStarted = false;

    String gAppliedHostname;
    String gAppliedMdnsHostname;

    StaticIpSettings gAppliedStaticSettings;

    StaticIpSettings loadStaticIpSettings();
    String sanitizeHostname(const String &raw, const char *fallback);
    void applyStationConfig();
    void startConnectAttempt(const __FlashStringHelper *reason);
    void stopMdns();
    void ensureMdns();
}

void wifiManagerInit()
{
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(false);
    WiFi.setAutoConnect(false);
    WiFi.persistent(false);
    WiFi.disconnect(true);

    gNextAttemptMillis = 0;
    gCurrentBackoffMs = kInitialBackoffMs;
    gAttemptCounter = 0;
    gWasConnected = false;
    gMdnsStarted = false;
    gAppliedHostname = String();
    gAppliedMdnsHostname = String();

    wifiManagerRequestReconnect(true);
}

void wifiManagerLoop()
{
    unsigned long now = millis();
    wl_status_t status = WiFi.status();

    if (status == WL_CONNECTED)
    {
        if (!gWasConnected)
        {
            gWasConnected = true;
            gCurrentBackoffMs = kInitialBackoffMs;
            gNextAttemptMillis = 0;
            IPAddress ip = WiFi.localIP();
            IPAddress gw = WiFi.gatewayIP();
            IPAddress mask = WiFi.subnetMask();
            String msg = F("WiFi connected. IP: ");
            msg += ip.toString();
            msg += F(" Gateway: ");
            msg += gw.toString();
            msg += F(" Netmask: ");
            msg += mask.toString();
            LOG_INFO(msg);
            Metrics::recordWifiConnected();
        }

        ensureMdns();
        return;
    }

    if (gWasConnected)
    {
        gWasConnected = false;
        gNextAttemptMillis = 0;
        LOG_WARN(F("WiFi link lost. Scheduling reconnect."));
        Metrics::recordWifiDisconnected();
    }

    stopMdns();

    bool reconnectDesired = false;
    bool immediate = false;
    portENTER_CRITICAL(&gReconnectMux);
    if (gReconnectRequested)
    {
        reconnectDesired = true;
        immediate = gImmediateRequested;
        gReconnectRequested = false;
        gImmediateRequested = false;
    }
    portEXIT_CRITICAL(&gReconnectMux);

    if (immediate)
    {
        gCurrentBackoffMs = kInitialBackoffMs;
        gNextAttemptMillis = 0;
    }

    if (!reconnectDesired && gNextAttemptMillis != 0 && now < gNextAttemptMillis)
    {
        return;
    }

    switch (status)
    {
    case WL_DISCONNECTED:
    case WL_CONNECTION_LOST:
    case WL_NO_SSID_AVAIL:
    case WL_IDLE_STATUS:
    default:
    {
        unsigned long usedBackoff = gCurrentBackoffMs;
        startConnectAttempt(reconnectDesired ? F("config change") : F("retry"));
        Metrics::recordWifiAttempt(gAttemptCounter, usedBackoff);
        gNextAttemptMillis = now + usedBackoff;
        if (usedBackoff < kMaxBackoffMs)
        {
            unsigned long next = usedBackoff * 2;
            gCurrentBackoffMs = (next > kMaxBackoffMs) ? kMaxBackoffMs : next;
        }
        else
        {
            gCurrentBackoffMs = kMaxBackoffMs;
        }
        break;
    }
    case WL_CONNECTED:
        // handled above
        break;
    }
}

void wifiManagerRequestReconnect(bool immediate)
{
    portENTER_CRITICAL(&gReconnectMux);
    gReconnectRequested = true;
    if (immediate)
    {
        gImmediateRequested = true;
    }
    portEXIT_CRITICAL(&gReconnectMux);
}

namespace
{
    StaticIpSettings loadStaticIpSettings()
    {
        StaticIpSettings s;
        s.enabled = AppConfig::get().getWifiStaticIpEnabled();
        if (!s.enabled)
        {
            return s;
        }

        auto parseIp = [](const String &text, IPAddress &out) -> bool
        {
            IPAddress tmp;
            if (!tmp.fromString(text.c_str()))
            {
                return false;
            }
            out = tmp;
            return true;
        };

        String ipStr = AppConfig::get().getWifiStaticIp();
        String gwStr = AppConfig::get().getWifiStaticGateway();
        String maskStr = AppConfig::get().getWifiStaticSubnet();
        String dns1Str = AppConfig::get().getWifiStaticDns1();
        String dns2Str = AppConfig::get().getWifiStaticDns2();

        bool okIp = parseIp(ipStr, s.ip);
        bool okGw = parseIp(gwStr, s.gateway);
        bool okMask = parseIp(maskStr, s.subnet);
        bool okDns1 = false;
        bool okDns2 = false;
        if (!dns1Str.isEmpty())
        {
            okDns1 = parseIp(dns1Str, s.dns1);
        }
        if (!dns2Str.isEmpty())
        {
            okDns2 = parseIp(dns2Str, s.dns2);
        }

        if (!okIp || !okGw || !okMask)
        {
            LOG_WARN(F("[WiFi] Static IP config incomplete or invalid; falling back to DHCP."));
            s.enabled = false;
            return s;
        }

        if (!okDns1)
        {
            s.dns1 = s.gateway;
        }
        if (!okDns2)
        {
            s.dns2 = s.dns1;
        }

        s.valid = true;
        return s;
    }

    String sanitizeHostname(const String &raw, const char *fallback)
    {
        String trimmed = raw;
        trimmed.trim();
        String result;
        result.reserve(trimmed.length());

        for (size_t i = 0; i < trimmed.length(); ++i)
        {
            char c = trimmed[i];
            if (isalnum(static_cast<unsigned char>(c)))
            {
                result += static_cast<char>(tolower(static_cast<unsigned char>(c)));
            }
            else if (c == '-' || c == '_' || c == ' ')
            {
                result += '-';
            }
        }

        while (result.startsWith("-"))
        {
            result.remove(0, 1);
        }
        while (result.endsWith("-"))
        {
            result.remove(result.length() - 1);
        }

        if (result.length() > kMaxHostnameLen)
        {
            result.remove(kMaxHostnameLen);
        }

        if (result.isEmpty())
        {
            if (fallback && fallback[0] != '\0')
            {
                result = sanitizeHostname(String(fallback), "");
            }
            if (result.isEmpty())
            {
                result = F("esp-sensor");
            }
        }

        return result;
    }

    void applyStationConfig()
    {
        String fallback = AppConfig::get().getDeviceLocation();
        String hostRaw = AppConfig::get().getWifiHostname();
        String sanitized = sanitizeHostname(hostRaw, fallback.c_str());

        if (sanitized != gAppliedHostname)
        {
            if (!sanitized.isEmpty())
            {
                WiFi.setHostname(sanitized.c_str());
                String msg = F("[WiFi] Hostname set to ");
                msg += sanitized;
                LOG_INFO(msg);
            }
            gAppliedHostname = sanitized;
        }

        StaticIpSettings desired = loadStaticIpSettings();

        if (desired.enabled && desired.valid)
        {
            bool sameAsCurrent = gAppliedStaticSettings.enabled && gAppliedStaticSettings.valid &&
                                 gAppliedStaticSettings.ip == desired.ip &&
                                 gAppliedStaticSettings.gateway == desired.gateway &&
                                 gAppliedStaticSettings.subnet == desired.subnet &&
                                 gAppliedStaticSettings.dns1 == desired.dns1 &&
                                 gAppliedStaticSettings.dns2 == desired.dns2;
            if (!sameAsCurrent)
            {
                String msg = F("[WiFi] Applying static IP ");
                msg += desired.ip.toString();
                msg += F(" gateway ");
                msg += desired.gateway.toString();
                msg += F(" netmask ");
                msg += desired.subnet.toString();
                LOG_INFO(msg);
                WiFi.config(desired.ip, desired.gateway, desired.subnet, desired.dns1, desired.dns2);
                gAppliedStaticSettings = desired;
            }
        }
        else
        {
            if (gAppliedStaticSettings.enabled)
            {
                LOG_INFO(F("[WiFi] Returning to DHCP."));
            }
            WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE, INADDR_NONE);
            gAppliedStaticSettings = StaticIpSettings{};
        }
    }

    void startConnectAttempt(const __FlashStringHelper *reason)
    {
        String ssid = AppConfig::get().getWifiSSID();
        String pass = AppConfig::get().getWifiPassword();
        ssid.trim();
        pass.trim();

        if (ssid.isEmpty())
        {
            LOG_WARN(F("[WiFi] SSID not configured; skipping connection attempt."));
            return;
        }

        applyStationConfig();

        ++gAttemptCounter;
        String msg = F("[WiFi] Connecting to SSID '");
        msg += ssid;
        msg += F("' (attempt #");
        msg += gAttemptCounter;
        msg += F(", reason: ");
        msg += String(reason);
        msg += ')';
        LOG_INFO(msg);

        WiFi.disconnect(false, false);
        if (pass.isEmpty())
        {
            WiFi.begin(ssid.c_str());
        }
        else
        {
            WiFi.begin(ssid.c_str(), pass.c_str());
        }
    }

    void stopMdns()
    {
        if (gMdnsStarted)
        {
            MDNS.end();
            gMdnsStarted = false;
            gAppliedMdnsHostname = String();
            LOG_INFO(F("[mDNS] Stopped."));
        }
    }

    void ensureMdns()
    {
        if (!WiFi.isConnected())
        {
            stopMdns();
            return;
        }

        String fallback = gAppliedHostname;
        String mdnsRaw = AppConfig::get().getMdnsHostname();
        String mdnsSanitized = sanitizeHostname(mdnsRaw, fallback.c_str());

        if (mdnsSanitized.isEmpty())
        {
            stopMdns();
            return;
        }

        if (gMdnsStarted && mdnsSanitized != gAppliedMdnsHostname)
        {
            MDNS.end();
            gMdnsStarted = false;
        }

        if (!gMdnsStarted)
        {
            if (MDNS.begin(mdnsSanitized.c_str()))
            {
                MDNS.addService("http", "tcp", 80);
                gAppliedMdnsHostname = mdnsSanitized;
                gMdnsStarted = true;
                String msg = F("[mDNS] Advertised as ");
                msg += mdnsSanitized;
                msg += F(".local");
                LOG_INFO(msg);
            }
            else
            {
                String msg = F("[mDNS] Failed to start for hostname ");
                msg += mdnsSanitized;
                LOG_WARN(msg);
            }
        }
    }
}
