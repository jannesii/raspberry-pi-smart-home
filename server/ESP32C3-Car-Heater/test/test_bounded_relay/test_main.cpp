#include <unity.h>
#include "RelayExecutor.h"

struct FakeRelay : RelayTransport {
    bool wifi = true;
    bool rpc = true;
    int requests = 0;
    std::string last;
    bool postRelay(const std::string& command) override {
        ++requests; last = command; return wifi && rpc;
    }
};
void test_execution_preserves_identity_finish_and_transport_failure() {
    FakeRelay relay;
    JsonDocument cmd;
    deserializeJson(cmd, R"({"action":"turn_on","device_boot_id":"boot-one","session_id":"one","issued_at":1800000000,"expires_at":1800000030,"finish_at":1800010800})");
    TEST_ASSERT_TRUE(RelayExecutor::execute(cmd.as<JsonObjectConst>(), 1800000001, relay, "boot-one"));
    JsonDocument delivered; deserializeJson(delivered, relay.last);
    TEST_ASSERT_EQUAL_STRING("one", delivered["session_id"]);
    TEST_ASSERT_EQUAL_INT64(1800010800, delivered["finish_at"].as<int64_t>());
    relay.wifi = false; relay.rpc = false;
    TEST_ASSERT_FALSE(RelayExecutor::execute(cmd.as<JsonObjectConst>(), 1800000002, relay, "boot-one"));
}
void test_off_bypasses_clock_and_duplicate_cooldown() {
    FakeRelay relay; JsonDocument cmd;
    cmd["action"] = "turn_off";
    TEST_ASSERT_TRUE(RelayExecutor::execute(cmd.as<JsonObjectConst>(), 0, relay, "boot-one"));
    TEST_ASSERT_TRUE(RelayExecutor::execute(cmd.as<JsonObjectConst>(), 0, relay, "boot-one"));
    TEST_ASSERT_EQUAL(2, relay.requests);
}
void test_obsolete_on_and_extension_are_rejected() {
    FakeRelay relay; JsonDocument cmd;
    deserializeJson(cmd, R"({"action":"turn_on","device_boot_id":"boot-one","session_id":"one","issued_at":1800000000,"expires_at":1800000030,"finish_at":1800010800})");
    TEST_ASSERT_FALSE(RelayExecutor::execute(cmd.as<JsonObjectConst>(), 1800000030, relay, "boot-one"));
    cmd["finish_at"] = 1800010801;
    TEST_ASSERT_FALSE(RelayExecutor::execute(cmd.as<JsonObjectConst>(), 1800000001, relay, "boot-one"));
    TEST_ASSERT_EQUAL(0, relay.requests);
}
void test_esp_restart_rejects_pre_restart_on_but_allows_off() {
    FakeRelay relay; JsonDocument cmd;
    deserializeJson(cmd, R"({"action":"turn_on","device_boot_id":"old-boot","issued_at":1800000000,"expires_at":1800000030})");
    TEST_ASSERT_FALSE(RelayExecutor::execute(cmd.as<JsonObjectConst>(), 1800000001, relay, "new-boot"));
    TEST_ASSERT_EQUAL(0, relay.requests);
    cmd["action"] = "turn_off";
    TEST_ASSERT_TRUE(RelayExecutor::execute(cmd.as<JsonObjectConst>(), 0, relay, "new-boot"));
}
int main() {
    UNITY_BEGIN();
    RUN_TEST(test_execution_preserves_identity_finish_and_transport_failure);
    RUN_TEST(test_off_bypasses_clock_and_duplicate_cooldown);
    RUN_TEST(test_esp_restart_rejects_pre_restart_on_but_allows_off);
    RUN_TEST(test_obsolete_on_and_extension_are_rejected);
    return UNITY_END();
}
