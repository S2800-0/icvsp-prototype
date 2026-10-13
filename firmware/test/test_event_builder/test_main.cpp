// Safety-event JSON in the engine's format (engine/schema/safety_event.schema.json, version 2).
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
    EventBuilder b("dev-01", "imu-v0", 7);
    char buf[640], id[64];
    const size_t n = b.build(pothole(), fix(), buf, sizeof buf, id, sizeof id);
    TEST_ASSERT_TRUE(n > 0);
    TEST_ASSERT_EQUAL(std::strlen(buf), n);
    TEST_ASSERT_EQUAL_STRING(
        "{\"schema_version\":2,\"event_id\":\"dev-01-7-1\",\"device_id\":\"dev-01\",\"boot_id\":7,\"sequence\":1,"
        "\"type\":\"pothole\",\"detected_at\":\"2026-09-21T14:13:20.123Z\",\"gps_valid\":true,"
        "\"latitude\":30.044412,\"longitude\":31.235712,\"position_accuracy_m\":3.5,"
        "\"road_segment\":\"cell:15022:15617\",\"speed_kmh\":42.0,\"heading_deg\":91.2,\"severity\":\"high\","
        "\"detection_confidence\":0.81,\"imu_peak_g\":0.94,\"model\":\"imu-v0\"}",
        buf);
    TEST_ASSERT_EQUAL_STRING("dev-01-7-1", id);
}

void test_ids_include_the_boot_so_they_never_repeat_after_a_restart() {
    char buf[640], id_a[64], id_b[64];
    EventBuilder before("dev-01", "imu-v0", 3), after("dev-01", "imu-v0", 4);
    before.build(pothole(), fix(), buf, sizeof buf, id_a, sizeof id_a);
    after.build(pothole(), fix(), buf, sizeof buf, id_b, sizeof id_b);  // sequence restarts at 1 after the reboot
    TEST_ASSERT_EQUAL_STRING("dev-01-3-1", id_a);
    TEST_ASSERT_EQUAL_STRING("dev-01-4-1", id_b);
}

void test_camera_only_report_has_no_imu_peak() {
    EventBuilder b("dev-01", "cam-v0", 1);
    Detection d = pothole();
    d.peak = 0;
    char buf[640];
    b.build(d, fix(), buf, sizeof buf);
    TEST_ASSERT_NULL(std::strstr(buf, "imu_peak_g"));
}

void test_event_ids_count_up() {
    EventBuilder b("dev-01", "imu-v0", 1);
    char buf[640], id[64];
    b.build(pothole(), fix(), buf, sizeof buf, id, sizeof id);
    b.build(pothole(), fix(), buf, sizeof buf, id, sizeof id);
    TEST_ASSERT_EQUAL_STRING("dev-01-1-2", id);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"event_id\":\"dev-01-1-2\""));
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"sequence\":2,"));
}

void test_speed_bump_type() {
    EventBuilder b("dev-01", "imu-v0", 1);
    Detection d = pothole();
    d.type = HazardType::SpeedBump;
    char buf[640];
    b.build(d, fix(), buf, sizeof buf);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"type\":\"speed_bump\""));
}

void test_no_position_fix_means_null_position_not_an_invented_one() {
    EventBuilder b("dev-01", "imu-v0", 1);
    char buf[640];
    GnssFix f = fix();
    f.valid = false;
    TEST_ASSERT_TRUE(b.build(pothole(), f, buf, sizeof buf) > 0);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"gps_valid\":false,\"latitude\":null,\"longitude\":null,"
                                          "\"position_accuracy_m\":null,\"speed_kmh\":null,\"heading_deg\":null,"));
    TEST_ASSERT_NULL(std::strstr(buf, "road_segment"));
    f = fix();
    f.acc_m = 0;  // a fix without an accuracy estimate is not trusted either
    b.build(pothole(), f, buf, sizeof buf);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"gps_valid\":false"));
}

void test_no_event_before_gnss_time_is_known() {
    EventBuilder b("dev-01", "imu-v0", 1);
    char buf[640];
    GnssFix f = fix();
    f.valid = false;
    f.utc_ms = 0;
    TEST_ASSERT_EQUAL(0, b.build(pothole(), f, buf, sizeof buf));
    TEST_ASSERT_EQUAL(1, b.next_seq());  // no ID used up
}

void test_buffer_too_small_is_refused() {
    EventBuilder b("dev-01", "imu-v0", 1);
    char buf[64];
    TEST_ASSERT_EQUAL(0, b.build(pothole(), fix(), buf, sizeof buf));
}

void test_heading_stays_in_range() {
    EventBuilder b("dev-01", "imu-v0", 1);
    char buf[640];
    GnssFix f = fix();
    f.heading_deg = 359.97f;  // would print as 360.0
    b.build(pothole(), f, buf, sizeof buf);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"heading_deg\":0.0,"));
    f.heading_deg = -90.0f;
    b.build(pothole(), f, buf, sizeof buf);
    TEST_ASSERT_NOT_NULL(std::strstr(buf, "\"heading_deg\":270.0,"));
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_utc_formatting);
    RUN_TEST(test_event_has_the_exact_expected_bytes);
    RUN_TEST(test_event_ids_count_up);
    RUN_TEST(test_speed_bump_type);
    RUN_TEST(test_ids_include_the_boot_so_they_never_repeat_after_a_restart);
    RUN_TEST(test_camera_only_report_has_no_imu_peak);
    RUN_TEST(test_no_position_fix_means_null_position_not_an_invented_one);
    RUN_TEST(test_no_event_before_gnss_time_is_known);
    RUN_TEST(test_buffer_too_small_is_refused);
    RUN_TEST(test_heading_stays_in_range);
    return UNITY_END();
}
