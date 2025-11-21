#include "core/TimeKeeper.h"

#include <time.h>
#include <stdlib.h>

namespace
{
  static bool g_valid = false;
  static time_t g_baseEpochSec = 0; // last synced epoch (seconds since Unix epoch)
  static uint32_t g_baseMillis = 0; // millis() snapshot when base was set

  // Europe/Helsinki timezone string (EET/EEST with DST)
  constexpr const char *TZ_EUROPE_HELSINKI = "EET-2EEST,M3.5.0/3,M10.5.0/4";
  constexpr const uint32_t SYNC_INTERVAL_MS = 60UL * 1000UL; // 1 minute
}

namespace timekeeper
{
  static void TimeKeeperTask(void *args);

  bool begin()
  {
    // Configure timezone to Europe/Helsinki and start SNTP
    setenv("TZ", TZ_EUROPE_HELSINKI, 1);
    tzset();

    // Use default NTP servers; offset handled by TZ
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");

    // Fetch initial time from the network (blocking with timeout)
    struct tm timeinfo;
    const int maxAttempts = 10;
    int attempts = 0;
    while (attempts < maxAttempts)
    {
      if (getLocalTime(&timeinfo, 5000))
      {
        g_baseEpochSec = mktime(&timeinfo);
        g_baseMillis = millis();
        g_valid = true;
        break;
      }
      ++attempts;
      delay(500);
    }

    if (!g_valid)
    {
      // Failed to obtain time
      return false;
    }

    xTaskCreate(
        TimeKeeperTask,
        "TimeKeeper",
        2048,
        nullptr,
        1,
        nullptr);

    return true;
  }

  bool isValid()
  {
    return g_valid;
  }

  // Internal helper: compute current epoch using last sync + millis()
  static time_t nowEpoch()
  {
    if (!g_valid)
      return 0;
    // millis() is 32-bit; subtraction handles wrap-around with unsigned arithmetic
    uint32_t nowMs = millis();
    uint32_t deltaMs = nowMs - g_baseMillis;
    return g_baseEpochSec + static_cast<time_t>(deltaMs / 1000UL);
  }

  // Helper: format epoch to string
  String formatEpoch(time_t epoch)
  {
    if (epoch == 0)
      return String("");
    struct tm tmv;
    localtime_r(&epoch, &tmv);
    char buf[32];
    // YYYY-MM-DD HH:MM:SS
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &tmv);
    return String(buf);
  }


  String formatUtc()
  {
    if (!g_valid)
      return String("");
    // We no longer track UTC separately; return local time for compatibility.
    return formatLocal();
  }

  String formatLocal()
  {
    if (!g_valid)
      return String("");
    return formatEpoch(nowEpoch());
  }

  static void TimeKeeperTask(void *args)
  {
    (void)args;
    for (;;)
    {
      struct tm timeinfo;
      if (getLocalTime(&timeinfo, 5000))
      {
        g_baseEpochSec = mktime(&timeinfo);
        g_baseMillis = millis();
        g_valid = true;
      }
      // Wait one minute between sync attempts
      vTaskDelay(pdMS_TO_TICKS(SYNC_INTERVAL_MS));
    }
  }
} // namespace timekeeper
