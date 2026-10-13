// Camera + IMU fusion into one report per hazard.
#include <unity.h>

#include "fusion.h"

using namespace icvsp;

namespace {
Detection imu(HazardType t, uint32_t t_ms, float conf = 0.7f) { return {t, t_ms, 6.0f, 100, Severity::Medium, conf}; }
CameraHazard cam(HazardType t, uint32_t t_ms, float conf = 0.6f) { return {t, t_ms - 400, t_ms, conf, 4, 0.5f, 0.5f}; }
}  // namespace

void setUp() {}
void tearDown() {}

void test_matching_imu_and_rear_camera_become_one_stronger_report() {
    Fusion f;
    FusedHazard out[4];
    f.add_imu(imu(HazardType::SpeedBump, 10000));
    TEST_ASSERT_EQUAL(0, f.poll(10500, out, 4));  // waiting: the rear camera may still confirm it
    f.add_camera(cam(HazardType::SpeedBump, 11200));
    TEST_ASSERT_EQUAL(1, f.poll(11200, out, 4));
    TEST_ASSERT_EQUAL(HazardType::SpeedBump, out[0].type);
    TEST_ASSERT_EQUAL(kSensorImu | kSensorCamera, out[0].sensors);
    TEST_ASSERT_FLOAT_WITHIN(1e-4f, 1.0f - 0.3f * 0.4f, out[0].confidence);  // 0.88
    TEST_ASSERT_EQUAL_UINT32(10000, out[0].t_ms);  // position of the impact
    TEST_ASSERT_FLOAT_WITHIN(1e-4f, 6.0f, out[0].imu_peak);  // jolt strength kept for the report
    TEST_ASSERT_EQUAL(0, f.poll(20000, out, 4));
}

void test_imu_alone_is_reported_after_the_window() {
    Fusion f;
    FusedHazard out[4];
    f.add_imu(imu(HazardType::SpeedBump, 10000));
    TEST_ASSERT_EQUAL(0, f.poll(12900, out, 4));
    TEST_ASSERT_EQUAL(1, f.poll(13100, out, 4));
    TEST_ASSERT_EQUAL(kSensorImu, out[0].sensors);
    TEST_ASSERT_FLOAT_WITHIN(1e-4f, 0.7f, out[0].confidence);
}

void test_camera_alone_is_reported_with_its_own_confidence() {
    Fusion f;
    FusedHazard out[4];
    f.add_camera(cam(HazardType::Pothole, 5000, 0.55f));
    TEST_ASSERT_EQUAL(1, f.poll(5000, out, 4));
    TEST_ASSERT_EQUAL(kSensorCamera, out[0].sensors);
    TEST_ASSERT_EQUAL(Severity::Low, out[0].severity);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, out[0].imu_peak);  // nothing felt
    TEST_ASSERT_FLOAT_WITHIN(1e-4f, 0.55f, out[0].confidence);
}

void test_camera_too_late_is_a_separate_report() {
    Fusion f;
    FusedHazard out[4];
    f.add_imu(imu(HazardType::Pothole, 10000));
    f.add_camera(cam(HazardType::Pothole, 13500));  // 3.5 s after the impact: outside the 3 s window
    TEST_ASSERT_EQUAL(2, f.poll(13500, out, 4));
    TEST_ASSERT_EQUAL(kSensorImu, out[0].sensors | 0);
}

void test_imu_decides_the_type_when_sensors_disagree() {
    Fusion f;
    FusedHazard out[4];
    f.add_imu(imu(HazardType::SpeedBump, 10000));
    f.add_camera(cam(HazardType::Pothole, 10800));
    TEST_ASSERT_EQUAL(1, f.poll(10800, out, 4));
    TEST_ASSERT_EQUAL(HazardType::SpeedBump, out[0].type);
    TEST_ASSERT_EQUAL(kSensorImu | kSensorCamera, out[0].sensors);
}

void test_forward_camera_waits_for_the_impact() {
    FusionConfig cfg;
    cfg.rear_camera = false;
    Fusion f(cfg);
    FusedHazard out[4];
    f.add_camera(cam(HazardType::Pothole, 8000));
    TEST_ASSERT_EQUAL(0, f.poll(9000, out, 4));  // the car has not reached it yet
    f.add_imu(imu(HazardType::Pothole, 10500));
    TEST_ASSERT_EQUAL(1, f.poll(10500, out, 4));
    TEST_ASSERT_EQUAL(kSensorImu | kSensorCamera, out[0].sensors);
}

void test_flush_reports_everything_pending() {
    Fusion f;
    FusedHazard out[4];
    f.add_imu(imu(HazardType::Pothole, 10000));
    TEST_ASSERT_EQUAL(1, f.poll(10100, out, 4, true));
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_matching_imu_and_rear_camera_become_one_stronger_report);
    RUN_TEST(test_imu_alone_is_reported_after_the_window);
    RUN_TEST(test_camera_alone_is_reported_with_its_own_confidence);
    RUN_TEST(test_camera_too_late_is_a_separate_report);
    RUN_TEST(test_imu_decides_the_type_when_sensors_disagree);
    RUN_TEST(test_forward_camera_waits_for_the_impact);
    RUN_TEST(test_flush_reports_everything_pending);
    return UNITY_END();
}
