// IMU hazard detector on synthetic drives (proposed FR-27).
#include <unity.h>

#include <vector>

#include "bump_detector.h"
#include "sim.h"

using namespace icvsp;
using sim::Feature;

namespace {

std::vector<Detection> run(const sim::Drive& d, BumpConfig cfg = {}) {
    BumpDetector det(cfg);
    std::vector<Detection> found;
    for (const ImuSample& s : d.imu()) {
        Detection out;
        if (det.update(s, d.speed_at(s.t_ms / 1000.0f), out)) found.push_back(out);
    }
    return found;
}

sim::Drive drive(std::vector<sim::Event> events, float speed = 40, float noise = 0.3f, float secs = 20,
                 uint32_t seed = 1) {
    sim::Drive d;
    d.events = std::move(events);
    d.speed_kmh = speed;
    d.noise = noise;
    d.duration_s = secs;
    d.seed = seed;
    return d;
}

}  // namespace

void setUp() {}
void tearDown() {}

void test_speed_bump_is_detected_once_as_bump() {
    auto found = run(drive({{Feature::SpeedBump, 5.0f, 4.0f}}, 20));
    TEST_ASSERT_EQUAL(1, found.size());
    TEST_ASSERT_EQUAL(HazardType::SpeedBump, found[0].type);
    TEST_ASSERT_UINT32_WITHIN(150, 5000, found[0].t_ms);
    TEST_ASSERT_GREATER_OR_EQUAL(200, found[0].duration_ms);
}

void test_pothole_is_detected_as_pothole() {
    auto found = run(drive({{Feature::Pothole, 8.0f, 10.0f}}));
    TEST_ASSERT_EQUAL(1, found.size());
    TEST_ASSERT_EQUAL(HazardType::Pothole, found[0].type);
    TEST_ASSERT_EQUAL(Severity::High, found[0].severity);
    TEST_ASSERT_UINT32_WITHIN(50, 8000, found[0].t_ms);
}

void test_severity_follows_the_size_of_the_jolt() {
    auto small = run(drive({{Feature::Pothole, 8.0f, 3.0f}}));
    auto medium = run(drive({{Feature::Pothole, 8.0f, 6.0f}}));
    TEST_ASSERT_EQUAL(1, small.size());
    TEST_ASSERT_EQUAL(1, medium.size());
    TEST_ASSERT_EQUAL(Severity::Low, small[0].severity);
    TEST_ASSERT_EQUAL(Severity::Medium, medium[0].severity);
    TEST_ASSERT_TRUE(medium[0].confidence > small[0].confidence);
}

void test_normal_vibration_gives_no_reports() {
    for (uint32_t seed = 1; seed <= 5; ++seed) TEST_ASSERT_EQUAL(0, run(drive({}, 40, 0.3f, 60, seed)).size());
}

void test_rough_road_raises_the_threshold() {
    // a rough road (RMS 0.8) gives no reports by itself, and a small jolt that would count on a smooth
    // road is ignored there, while a large one is still found
    for (uint32_t seed = 1; seed <= 5; ++seed) TEST_ASSERT_EQUAL(0, run(drive({}, 40, 0.8f, 60, seed)).size());
    TEST_ASSERT_EQUAL(1, run(drive({{Feature::Pothole, 8.0f, 2.5f}}, 40, 0.3f)).size());
    TEST_ASSERT_EQUAL(0, run(drive({{Feature::Pothole, 8.0f, 2.5f}}, 40, 0.8f)).size());
    TEST_ASSERT_EQUAL(1, run(drive({{Feature::Pothole, 8.0f, 8.0f}}, 40, 0.8f)).size());
}

void test_braking_is_not_a_hazard() {
    TEST_ASSERT_EQUAL(0, run(drive({{Feature::Braking, 5.0f, 5.0f}}, 60)).size());
}

void test_jolts_while_parked_are_ignored() {
    TEST_ASSERT_EQUAL(0, run(drive({{Feature::DoorSlam, 5.0f, 8.0f}}, 0)).size());
    // the same knock while driving does count, so it is the speed check that rejects it
    TEST_ASSERT_EQUAL(1, run(drive({{Feature::DoorSlam, 5.0f, 8.0f}}, 40)).size());
}

void test_one_report_per_hazard_but_separate_hazards_are_kept() {
    // front and rear wheels hit the same pothole 0.3 s apart: one report
    auto same = run(drive({{Feature::Pothole, 5.0f, 8.0f}, {Feature::Pothole, 5.3f, 8.0f}}));
    TEST_ASSERT_EQUAL(1, same.size());
    auto two = run(drive({{Feature::Pothole, 5.0f, 8.0f}, {Feature::SpeedBump, 9.0f, 4.0f}}));
    TEST_ASSERT_EQUAL(2, two.size());
    TEST_ASSERT_EQUAL(HazardType::Pothole, two[0].type);
    TEST_ASSERT_EQUAL(HazardType::SpeedBump, two[1].type);
}

void test_nothing_is_reported_during_warmup() {
    TEST_ASSERT_EQUAL(0, run(drive({{Feature::Pothole, 0.5f, 10.0f}})).size());
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_speed_bump_is_detected_once_as_bump);
    RUN_TEST(test_pothole_is_detected_as_pothole);
    RUN_TEST(test_severity_follows_the_size_of_the_jolt);
    RUN_TEST(test_normal_vibration_gives_no_reports);
    RUN_TEST(test_rough_road_raises_the_threshold);
    RUN_TEST(test_braking_is_not_a_hazard);
    RUN_TEST(test_jolts_while_parked_are_ignored);
    RUN_TEST(test_one_report_per_hazard_but_separate_hazards_are_kept);
    RUN_TEST(test_nothing_is_reported_during_warmup);
    return UNITY_END();
}
