#include "core/TimeKeeper.h"

#include <Preferences.h>
#include <time.h>

namespace
{
  Preferences prefs;

  // Base reference when clock was last set
  static bool g_valid = false;
  static bool g_trulyValid = false;
  static uint64_t g_baseEpochSec = 0; // seconds since Unix epoch (UTC)
  static uint32_t g_baseMillis = 0;   // millis() snapshot when base was set
  static int16_t g_tzOffsetMin = 0;   // minutes east of UTC

  constexpr const char *NAMESPACE = "clock";
  constexpr const char *KEY_TZ = "tz_min";
  constexpr const char *KEY_LAST_UTC = "last_epoch_sec";

}

namespace timekeeper
{
  bool begin()
  {
    if (!prefs.begin(NAMESPACE, /*readOnly*/ false))
    {
      return false;
    }
    // Seed timezone key if missing
    if (!prefs.isKey(KEY_TZ))
    {
      prefs.putShort(KEY_TZ, 0);
    }
    // Load last known UTC epoch
    if (prefs.isKey(KEY_LAST_UTC))
    {
      g_baseEpochSec = prefs.getULong64(KEY_LAST_UTC);
      g_baseMillis = millis();
      g_valid = true;
    }
    // Load timezone offset
    g_tzOffsetMin = prefs.getShort(KEY_TZ, 0);

    // Time base is not persisted across boots (no RTC), mark invalid
    if (!g_valid)
    {
      g_valid = false;
      g_baseEpochSec = 0;
      g_baseMillis = 0;
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

  bool isTrulyValid()
  {
    return g_trulyValid;
  }

  void setUtc(uint64_t epochSeconds)
  {
    g_baseEpochSec = epochSeconds;
    g_baseMillis = millis();
    g_valid = true;
    g_trulyValid = true;
  }

  void setUtcWithOffset(uint64_t epochSeconds, int16_t offsetMinutes)
  {
    setUtc(epochSeconds);
    setTzOffsetMinutes(offsetMinutes);
  }

  uint64_t nowUtc()
  {
    if (!g_valid)
      return 0;
    // millis() is 32-bit; subtraction handles wrap-around with unsigned arithmetic
    uint32_t nowMs = millis();
    uint32_t deltaMs = nowMs - g_baseMillis;
    return g_baseEpochSec + static_cast<uint64_t>(deltaMs) / 1000ULL;
  }

  int16_t tzOffsetMinutes()
  {
    return g_tzOffsetMin;
  }

  void setTzOffsetMinutes(int16_t minutes)
  {
    // Clamp to sensible range (-14h..+14h)
    if (minutes < -14 * 60)
      minutes = -14 * 60;
    if (minutes > 14 * 60)
      minutes = 14 * 60;
    g_tzOffsetMin = minutes;
    prefs.putShort(KEY_TZ, g_tzOffsetMin);
  }

  // Helper: format epoch to string
  String formatEpoch(uint64_t epoch)
  {
    if (epoch == 0)
      return String("");
    time_t t = static_cast<time_t>(epoch);
    struct tm tmv;
    gmtime_r(&t, &tmv);
    char buf[32];
    // YYYY-MM-DD HH:MM:SS
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &tmv);
    return String(buf);
  }


  String formatUtc()
  {
    if (!g_valid)
      return String("");
    return formatEpoch(nowUtc());
  }

  String formatLocal()
  {
    if (!g_valid)
      return String("");
    uint64_t local = nowUtc() + static_cast<int32_t>(g_tzOffsetMin) * 60;
    return formatEpoch(local);
  }

  int localMinutesOfDay()
  {
    if (!g_valid)
      return -1;
    int64_t local = static_cast<int64_t>(nowUtc()) + static_cast<int32_t>(g_tzOffsetMin) * 60;
    // Normalize to [0, 86399]
    int64_t secDay = local % 86400;
    if (secDay < 0)
      secDay += 86400;
    return static_cast<int>(secDay / 60);
  }

  static void TimeKeeperTask(void *args)
  {
    for (;;)
    {
      vTaskDelay(pdMS_TO_TICKS(1000)); // every second
      if (g_valid)
      {
        // Persist last known epoch time
        prefs.putULong64(KEY_LAST_UTC, nowUtc());
      }
    }
  }
} // namespace timekeeper
