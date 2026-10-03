// Safety-event JSON in the engine's format (engine/schema/safety_event.schema.json).
#include <unity.h>

#include <cstring>
#include <string>

#include "event_builder.h"
#include "sim.h"

using namespace icvsp;

namespace {

Detection pothole() { return {HazardType::Pothole, 8000, 9.2f, 60, Severity::High, 0.81f}; }

GnssFix fix() {
    GnssFix f;
    f.valid = true;
    f.lat = 30.044412;
    f.lon = 31.235712;
    f.acc_m = 3.5f;
    f.speed_kmh = 42.0f;
    f.heading_deg = 91.25f;
    f.utc_ms = 1790000000123;
    return f;
}

}  // namespace

void setUp() {}
void tearDown() {}

void test_utc_formatting() {
    char ts[25];
    format_utc(0, ts);
    TEST_ASSERT_EQUAL_STRING("1970-01-01T00:00:00.000Z", ts);
    format_utc(1790000000123, ts);
    TEST_ASSERT_EQUAL_STRING("2026-09-21T14:13:20.123Z", ts);
    format_utc(951782400000, ts);  // leap day
    TEST_ASSERT_EQUAL_STRING("2000-02-29T00:00:00.000Z", ts);
}

void test_event_has_the_exact_expected_bytes() {
    EventBuilder b("dev-01", "imu-v0");
    char buf[512], id[48];
    const size_t n = b.build(pothole(), fix(), buf, sizeof buf, id, sizeof id);
    TEST_ASSERT_TRUE(n > 0);
    TEST_ASSERT_EQUAL(std::strlen(buf), n);
    TEST_ASSERT_EQUAL_STRING(
        "{\"event_id\":\"dev-01-1\",\"type\":\"pothole\",\"timestamp\":\"2026-09-21T14:13:20.123Z\","
        "\"location\":{\"lat\":30.044412,\"lon\":31.235712,\"acc_m\":3.5},\"road_segment\":\"cell:15022:15617\","
        "\"speed_kmh\":42.0,\"heading\":91.2,\"severity\":\"high\",\"confidence\":0.81,"
        "\"source\":\"dev-01\",\"model\":\"imu-v0\"}",
        buf);
    TEST_ASSERT_EQUAL_STRING("dev-01-1", id);
}

void test_event_ids_count_up() {
    EventBuilder b("dev-01", "imu-v0");
    char buf[512], id[48];
    b.build(pothole(), fix(), buf, sizeof buf, id, sizeof id);
    b.build(pothole(), fix(), buf, sizeof buf, id, sizeof id);
    TEST_ASSERT_EQUAL_STRING("dev-01-2", id);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"event_id\":\"dev-01-2\""));
}

void test_speed_bump_type() {
    EventBuilder b("dev-01", "imu-v0");
    Detection d = pothole();
    d.type = HazardType::SpeedBump;
    char buf[512];
    b.build(d, fix(), buf, sizeof buf);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"type\":\"speed_bump\""));
}

void test_no_event_without_a_valid_fix() {
    EventBuilder b("dev-01", "imu-v0");
    char buf[512];
    GnssFix f = fix();
    f.valid = false;
    TEST_ASSERT_EQUAL(0, b.build(pothole(), f, buf, sizeof buf));
    f = fix();
    f.acc_m = 0;
    TEST_ASSERT_EQUAL(0, b.build(pothole(), f, buf, sizeof buf));
    TEST_ASSERT_EQUAL(1, b.next_seq());  // no ID used up
}

void test_buffer_too_small_is_refused() {
    EventBuilder b("dev-01", "imu-v0");
    char buf[64];
    TEST_ASSERT_EQUAL(0, b.build(pothole(), fix(), buf, sizeof buf));
}

void test_heading_stays_in_range() {
    EventBuilder b("dev-01", "imu-v0");
    char buf[512];
    GnssFix f = fix();
    f.heading_deg = 359.97f;  // would print as 360.0
    b.build(pothole(), f, buf, sizeof buf);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"heading\":0.0,"));
    f.heading_deg = -90.0f;
    b.build(pothole(), f, buf, sizeof buf);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"heading\":270.0,"));
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_utc_formatting);
    RUN_TEST(test_event_has_the_exact_expected_bytes);
    RUN_TEST(test_event_ids_count_up);
    RUN_TEST(test_speed_bump_type);
    RUN_TEST(test_no_event_without_a_valid_fix);
    RUN_TEST(test_buffer_too_small_is_refused);
    RUN_TEST(test_heading_stays_in_range);
    return UNITY_END();
}
