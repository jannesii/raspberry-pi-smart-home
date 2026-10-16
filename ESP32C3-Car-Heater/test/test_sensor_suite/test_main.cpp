// Measurement-boundary tests: SensorSuite against a simulated I2C bus.
#include <unity.h>

#include <ArduinoJson.h>

#include "SensorJson.h"
#include "SensorSuite.h"
#include "fake_bus.h"

static constexpr SensorAddresses kAddresses{0x44, 0x48, 0x49};

struct Rig
{
    FakeBus bus;
    FakeSht45 cabin;
    FakeTmp117 glassReference;
    FakeTmp117 glassComparison;

    Rig()
    {
        bus.sht45s[0x44] = &cabin;
        bus.tmp117s[0x48] = &glassReference;
        bus.tmp117s[0x49] = &glassComparison;
        cabin.set(21.0f, 45.0f);
        glassReference.setTemperature(-1.25f);
        glassComparison.setTemperature(2.5f);
    }
};

void setUp() {}
void tearDown() {}

static void test_reads_all_three_sensors_at_their_configured_addresses()
{
    Rig rig;
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);

    SensorSnapshot snap = suite.sample(1000);

    TEST_ASSERT_EQUAL(ReadingStatus::Ok, snap.glassReference.status);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, -1.25f, snap.glassReference.temperatureC);
    TEST_ASSERT_EQUAL(ReadingStatus::Ok, snap.glassComparison.status);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 2.5f, snap.glassComparison.temperatureC);
    TEST_ASSERT_EQUAL(ReadingStatus::Ok, snap.cabin.status);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 21.0f, snap.cabin.temperatureC);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 45.0f, snap.cabin.humidityPct);
    TEST_ASSERT_EQUAL_UINT32(0, snap.cabin.ageMs);
}

static void test_absent_and_misidentified_sensors_are_missing_with_distinct_errors()
{
    Rig rig;
    rig.glassComparison.present = false;     // nothing answers at 0x49
    rig.glassReference.deviceId = 0x0000; // a different chip answers at 0x48
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);

    SensorSnapshot snap = suite.sample(1000);

    TEST_ASSERT_EQUAL(ReadingStatus::Missing, snap.glassComparison.status);
    TEST_ASSERT_EQUAL(SensorError::NotFound, snap.glassComparison.error);
    TEST_ASSERT_EQUAL(ReadingStatus::Missing, snap.glassReference.status);
    TEST_ASSERT_EQUAL(SensorError::WrongIdentity, snap.glassReference.error);
    TEST_ASSERT_EQUAL(ReadingStatus::Ok, snap.cabin.status);
    TEST_ASSERT_EQUAL(SensorError::None, snap.cabin.error);
}

static void test_failed_read_reports_last_valid_value_as_cached_with_its_age()
{
    Rig rig;
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);
    suite.sample(1000);

    rig.glassComparison.failReads = true;
    rig.glassComparison.setTemperature(9.0f); // never observed
    SensorSnapshot snap = suite.sample(6000);

    TEST_ASSERT_EQUAL(ReadingStatus::Cached, snap.glassComparison.status);
    TEST_ASSERT_EQUAL(SensorError::ReadFailed, snap.glassComparison.error);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 2.5f, snap.glassComparison.temperatureC);
    TEST_ASSERT_EQUAL_UINT32(5000, snap.glassComparison.ageMs);
    TEST_ASSERT_EQUAL(ReadingStatus::Ok, snap.glassReference.status);
    TEST_ASSERT_EQUAL_UINT32(0, snap.glassReference.ageMs);
}

static void test_failed_read_without_a_previous_value_is_missing()
{
    Rig rig;
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);
    rig.cabin.failReads = true;

    SensorSnapshot snap = suite.sample(1000);

    TEST_ASSERT_EQUAL(ReadingStatus::Missing, snap.cabin.status);
    TEST_ASSERT_EQUAL(SensorError::ReadFailed, snap.cabin.error);
}

static void test_corrupted_sht45_frame_is_rejected_and_keeps_last_valid_value()
{
    Rig rig;
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);
    suite.sample(1000);

    rig.cabin.corruptCrc = true;
    rig.cabin.set(30.0f, 80.0f);
    SensorSnapshot snap = suite.sample(2000);

    TEST_ASSERT_EQUAL(ReadingStatus::Cached, snap.cabin.status);
    TEST_ASSERT_EQUAL(SensorError::CrcMismatch, snap.cabin.error);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 21.0f, snap.cabin.temperatureC);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 45.0f, snap.cabin.humidityPct);
}

static void test_out_of_range_sht45_temperature_is_rejected()
{
    Rig rig;
    rig.cabin.temperatureRaw = 0x0000; // -45 C: below the SHT45 operating range
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);

    SensorSnapshot snap = suite.sample(1000);

    TEST_ASSERT_EQUAL(ReadingStatus::Missing, snap.cabin.status);
    TEST_ASSERT_EQUAL(SensorError::OutOfRange, snap.cabin.error);
}

static void test_sht45_humidity_slightly_outside_physical_range_is_clipped()
{
    Rig rig;
    rig.cabin.humidityRaw = 0xFFFF; // 119 %RH before clipping
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);

    SensorSnapshot snap = suite.sample(1000);

    TEST_ASSERT_EQUAL(ReadingStatus::Ok, snap.cabin.status);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 100.0f, snap.cabin.humidityPct);
}

static void test_tmp117_power_on_value_is_not_a_measurement()
{
    Rig rig;
    rig.glassReference.temperatureRaw = 0x8000; // -256 C until the first conversion
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);

    SensorSnapshot snap = suite.sample(1000);

    TEST_ASSERT_EQUAL(ReadingStatus::Missing, snap.glassReference.status);
    TEST_ASSERT_EQUAL(SensorError::NotReady, snap.glassReference.error);
}

static void test_true_zero_degrees_is_a_valid_reading()
{
    Rig rig;
    rig.glassReference.setTemperature(0.0f);
    rig.cabin.set(0.0f, 0.0f);
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);

    SensorSnapshot snap = suite.sample(1000);

    TEST_ASSERT_EQUAL(ReadingStatus::Ok, snap.glassReference.status);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, snap.glassReference.temperatureC);
    TEST_ASSERT_EQUAL(ReadingStatus::Ok, snap.cabin.status);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 0.0f, snap.cabin.temperatureC);
}

static void test_sensor_missing_at_boot_is_reprobed_after_the_retry_interval()
{
    Rig rig;
    rig.glassComparison.present = false;
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);
    TEST_ASSERT_EQUAL(ReadingStatus::Missing, suite.sample(1000).glassComparison.status);

    rig.glassComparison.present = true; // connector reseated
    TEST_ASSERT_EQUAL(ReadingStatus::Missing, suite.sample(SensorSuite::kReprobeIntervalMs - 1).glassComparison.status);

    SensorSnapshot snap = suite.sample(SensorSuite::kReprobeIntervalMs);
    TEST_ASSERT_EQUAL(ReadingStatus::Ok, snap.glassComparison.status);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 2.5f, snap.glassComparison.temperatureC);
}

static void test_repeated_read_failures_trigger_reidentification()
{
    Rig rig;
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);
    suite.sample(1000);

    rig.glassReference.present = false; // sensor disconnected
    uint32_t now = 1000;
    for (uint32_t i = 0; i < SensorSuite::kFailuresBeforeReprobe; ++i)
    {
        now += 5000;
        TEST_ASSERT_EQUAL(SensorError::ReadFailed, suite.sample(now).glassReference.error);
    }
    SensorSnapshot waiting = suite.sample(now + 1000); // before the re-probe is due
    TEST_ASSERT_EQUAL(ReadingStatus::Cached, waiting.glassReference.status);
    TEST_ASSERT_EQUAL(SensorError::ReadFailed, waiting.glassReference.error);

    now += SensorSuite::kReprobeIntervalMs;
    SensorSnapshot snap = suite.sample(now);

    TEST_ASSERT_EQUAL(ReadingStatus::Cached, snap.glassReference.status);
    TEST_ASSERT_EQUAL(SensorError::NotFound, snap.glassReference.error);
    TEST_ASSERT_EQUAL_UINT32(now - 1000, snap.glassReference.ageMs);
}

static void test_status_json_distinguishes_fresh_cached_and_missing_sensors()
{
    Rig rig;
    rig.glassComparison.present = false;
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);
    suite.sample(1000);
    rig.glassReference.failReads = true;
    SensorSnapshot snap = suite.sample(4000);

    JsonDocument doc;
    writeSensorStatusJson(doc.to<JsonObject>(), snap);

    JsonObject cabin = doc["sensors"]["cabin"];
    TEST_ASSERT_EQUAL_STRING("ok", cabin["status"]);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 21.0f, cabin["temperature_c"].as<float>());
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 45.0f, cabin["humidity_pct"].as<float>());
    TEST_ASSERT_EQUAL_UINT32(0, cabin["age_ms"].as<uint32_t>());
    TEST_ASSERT_FALSE(cabin["error"].is<const char *>());
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 21.0f, doc["temperature"].as<float>());

    JsonObject reference = doc["sensors"]["glass_reference"];
    TEST_ASSERT_EQUAL_STRING("cached", reference["status"]);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, -1.25f, reference["temperature_c"].as<float>());
    TEST_ASSERT_EQUAL_UINT32(3000, reference["age_ms"].as<uint32_t>());
    TEST_ASSERT_EQUAL_STRING("read_failed", reference["error"]);
    TEST_ASSERT_FALSE(reference["humidity_pct"].is<float>());

    JsonObject lower = doc["sensors"]["glass_comparison"];
    TEST_ASSERT_EQUAL_STRING("missing", lower["status"]);
    TEST_ASSERT_TRUE(lower["temperature_c"].isNull());
    TEST_ASSERT_TRUE(lower.containsKey("temperature_c")); // explicit null, not omitted
    TEST_ASSERT_TRUE(lower["age_ms"].isNull());
    TEST_ASSERT_EQUAL_STRING("not_found", lower["error"]);
}

static void test_legacy_temperature_is_null_unless_cabin_was_read_now()
{
    Rig rig;
    SensorSuite suite(rig.bus, kAddresses);
    suite.begin(0);
    suite.sample(1000);
    rig.cabin.failReads = true;
    SensorSnapshot snap = suite.sample(2000);

    JsonDocument doc;
    writeSensorStatusJson(doc.to<JsonObject>(), snap);

    TEST_ASSERT_TRUE(doc.containsKey("temperature"));
    TEST_ASSERT_TRUE(doc["temperature"].isNull());
    TEST_ASSERT_EQUAL_STRING("cached", doc["sensors"]["cabin"]["status"]);
}

int main()
{
    UNITY_BEGIN();
    RUN_TEST(test_reads_all_three_sensors_at_their_configured_addresses);
    RUN_TEST(test_absent_and_misidentified_sensors_are_missing_with_distinct_errors);
    RUN_TEST(test_failed_read_reports_last_valid_value_as_cached_with_its_age);
    RUN_TEST(test_failed_read_without_a_previous_value_is_missing);
    RUN_TEST(test_corrupted_sht45_frame_is_rejected_and_keeps_last_valid_value);
    RUN_TEST(test_out_of_range_sht45_temperature_is_rejected);
    RUN_TEST(test_sht45_humidity_slightly_outside_physical_range_is_clipped);
    RUN_TEST(test_tmp117_power_on_value_is_not_a_measurement);
    RUN_TEST(test_true_zero_degrees_is_a_valid_reading);
    RUN_TEST(test_sensor_missing_at_boot_is_reprobed_after_the_retry_interval);
    RUN_TEST(test_repeated_read_failures_trigger_reidentification);
    RUN_TEST(test_status_json_distinguishes_fresh_cached_and_missing_sensors);
    RUN_TEST(test_legacy_temperature_is_null_unless_cabin_was_read_now);
    return UNITY_END();
}
