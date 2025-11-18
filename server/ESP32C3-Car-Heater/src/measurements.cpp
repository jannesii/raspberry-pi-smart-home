#include <Adafruit_BMP280.h>

#include "measurements.h"

Adafruit_BMP280 bmp;
static Measurements g_last_valid{NAN, NAN, NAN};
static bool g_have_valid = false;
static unsigned long g_last_ms = 0;
static unsigned long g_last_fault_log_ms = 0;

static bool try_init_addr_pins(uint8_t address, uint8_t sda, uint8_t scl) {
    Wire.begin(sda, scl);
    delay(10);
    if (bmp.begin(address)) {
        Serial.printf("BMP280 found at 0x%02X (SDA=%u, SCL=%u)\n", address, sda, scl);
        // Use forced mode so we explicitly trigger a fresh conversion each read
        bmp.setSampling(Adafruit_BMP280::MODE_FORCED,     /* Operating Mode. */
                        Adafruit_BMP280::SAMPLING_X2,     /* Temp. oversampling */
                        Adafruit_BMP280::SAMPLING_X16,    /* Pressure oversampling */
                        Adafruit_BMP280::FILTER_X16,      /* Filtering. */
                        Adafruit_BMP280::STANDBY_MS_500); /* Standby time. */
        return true;
    }
    return false;
}

bool initBMP280(uint8_t address, uint8_t sda, uint8_t scl) {
    // Try caller-provided first, then common ESP32-C3 pairs and both addresses.
    // Prefer 6/7 to avoid conflict with LED on IO8 on many SuperMini boards.
    if (try_init_addr_pins(address, sda, scl)) return true;
    const uint8_t alt_addr = (address == 0x76) ? 0x77 : 0x76;
    const uint8_t sda_opts[] = {sda, 6, 4, 8};
    const uint8_t scl_opts[] = {scl, 7, 5, 9};

    const size_t count = sizeof(sda_opts) / sizeof(sda_opts[0]);
    for (size_t i = 0; i < count; ++i) {
        uint8_t sda_i = sda_opts[i];
        uint8_t scl_i = scl_opts[i];
        if (try_init_addr_pins(address, sda_i, scl_i)) return true;
        if (try_init_addr_pins(alt_addr, sda_i, scl_i)) return true;
    }

    Serial.println("Could not find a valid BMP280 sensor on common I2C pins (6/7, 4/5, 8/9) or addresses (0x76/0x77). Check wiring.");
    return false;
}

Measurements takeMeasurement(bool verbose) {
    Measurements m;
    // In forced mode, trigger a new conversion before reading
    if (!bmp.takeForcedMeasurement()) {
        Serial.println("[BMP280] Forced measurement failed; returning last value if available.");
        unsigned long now = millis();
        if (now - g_last_fault_log_ms > 10000UL) {
            g_last_fault_log_ms = now;
        }
    }
    m.temperature = bmp.readTemperature();
    m.pressure    = bmp.readPressure() / 100.0F;
    m.altitude    = bmp.readAltitude(1013.25);

    bool temp_ok = (m.temperature > -40.0f && m.temperature < 85.0f);
    bool pres_ok = (m.pressure > 300.0f && m.pressure < 1100.0f);

    if (!temp_ok || !pres_ok) {
        Serial.println("[BMP280] Invalid reading detected (I2C glitch?). Keeping last value.");
        unsigned long now = millis();
        if (now - g_last_fault_log_ms > 10000UL) {
            g_last_fault_log_ms = now;
        }
    } else {
        g_last_valid = m;
        g_have_valid = true;
        g_last_ms = millis();
    }

    const Measurements& out = g_have_valid ? g_last_valid : m;
    if (verbose) {
        Serial.print("T: ");
        Serial.print(out.temperature, 2);
        Serial.print(" °C  |  P: ");
        Serial.print(out.pressure, 2);
        Serial.print(" hPa  |  Alt: ");
        Serial.print(out.altitude, 2);
        Serial.println(" m");
    }
    return out;
}

bool getLastMeasurement(Measurements& out, uint32_t& age_ms) {
    if (!g_have_valid) return false;
    out = g_last_valid;
    unsigned long now = millis();
    age_ms = (now >= g_last_ms) ? (now - g_last_ms) : 0;
    return true;
}
