// Camera decision logic: confidence, road check, 3-of-5 frames, motion check (FR-01, FR-27 support).
#include <unity.h>

#include <vector>

#include "hazard_tracker.h"

using namespace icvsp;

namespace {

constexpr uint32_t kFrameMs = 125;  // 8 frames per second, as expected for ESPDet-Pico on the ESP32-S3

CamDetection box(HazardType t, float cx, float cy, float w, float conf = 0.6f) {
    return CamDetection{t, conf, cx - w / 2, cy - 0.02f, cx + w / 2, cy + 0.02f, -1.0f};
}

// Feeds one detection per frame (or none where `present` is false); returns all confirmed hazards.
std::vector<CameraHazard> run(HazardTracker& tr, const std::vector<CamDetection>& per_frame,
                              const std::vector<bool>& present, float speed = 40.0f) {
    std::vector<CameraHazard> found;
    CameraHazard out[4];
    for (size_t f = 0; f < per_frame.size(); ++f) {
        const size_t n = tr.update(static_cast<uint32_t>(1000 + f * kFrameMs), speed, &per_frame[f],
                                   present[f] ? 1 : 0, out, 4);
        found.insert(found.end(), out, out + n);
    }
    return found;
}

// A speed bump seen by the rear camera: moves up the image and shrinks as the car drives away.
std::vector<CamDetection> receding_bump(int frames) {
    std::vector<CamDetection> v;
    for (int f = 0; f < frames; ++f) v.push_back(box(HazardType::SpeedBump, 0.5f, 0.85f - 0.04f * f, 0.30f - 0.02f * f));
    return v;
}

}  // namespace

void setUp() {}
void tearDown() {}

void test_receding_bump_is_confirmed_once() {
    HazardTracker tr;  // rear camera by default
    auto found = run(tr, receding_bump(6), std::vector<bool>(6, true));
    TEST_ASSERT_EQUAL(1, found.size());
    TEST_ASSERT_EQUAL(HazardType::SpeedBump, found[0].type);
    TEST_ASSERT_EQUAL_UINT32(1000, found[0].t_first_ms);
}

void test_single_frame_flicker_is_not_reported() {
    HazardTracker tr;
    std::vector<CamDetection> v(5, box(HazardType::Pothole, 0.5f, 0.7f, 0.1f));
    auto found = run(tr, v, {true, false, false, false, false});
    TEST_ASSERT_EQUAL(0, found.size());
}

void test_three_of_five_frames_confirms_a_pothole() {
    HazardTracker tr;
    std::vector<CamDetection> v(5, box(HazardType::Pothole, 0.5f, 0.7f, 0.1f, 0.3f));  // weak boxes
    TEST_ASSERT_EQUAL(0, run(tr, v, {true, false, true, false, false}).size());  // only 2 of 5
    HazardTracker tr2;
    TEST_ASSERT_EQUAL(1, run(tr2, v, {true, false, true, false, true}).size());  // 3 of 5
}

void test_a_confident_pothole_seen_in_two_frames_is_confirmed() {
    // potholes pass a forward camera in about 2 frames; a confident box makes 2 of 5 enough
    HazardTracker tr;
    std::vector<CamDetection> v(5, box(HazardType::Pothole, 0.5f, 0.7f, 0.1f, 0.6f));
    TEST_ASSERT_EQUAL(1, run(tr, v, {true, true, false, false, false}).size());
    TrackerConfig off;
    off.pothole_k_hits = 0;   // the rule can be switched off: back to 3 of 5
    HazardTracker tr2(off);
    TEST_ASSERT_EQUAL(0, run(tr2, v, {true, true, false, false, false}).size());
}

void test_two_frames_are_not_enough_for_a_speed_bump() {
    // the short rule is for potholes only: a bump still needs 3 frames and the motion check
    TrackerConfig fwd;
    fwd.rear_camera = false;
    HazardTracker tr(fwd);
    std::vector<CamDetection> v;
    for (int f = 0; f < 5; ++f) v.push_back(box(HazardType::SpeedBump, 0.5f, 0.6f + 0.05f * f, 0.2f + 0.02f * f, 0.9f));
    TEST_ASSERT_EQUAL(0, run(tr, v, {true, true, false, false, false}).size());
}

void test_static_bump_box_like_a_kerb_is_rejected() {
    // a kerb or lane edge: boxed at the same place in every frame, so it does not move like a fixed hazard
    HazardTracker tr;
    std::vector<CamDetection> v(8, box(HazardType::SpeedBump, 0.3f, 0.75f, 0.2f));
    TEST_ASSERT_EQUAL(0, run(tr, v, std::vector<bool>(8, true)).size());
}

void test_bump_moving_the_wrong_way_is_rejected() {
    // rear camera, but the box moves down and grows (e.g. a vehicle catching up): not a fixed hazard
    HazardTracker tr;
    std::vector<CamDetection> v;
    for (int f = 0; f < 6; ++f) v.push_back(box(HazardType::SpeedBump, 0.5f, 0.6f + 0.04f * f, 0.1f + 0.02f * f));
    TEST_ASSERT_EQUAL(0, run(tr, v, std::vector<bool>(6, true)).size());
}

void test_forward_camera_expects_the_opposite_motion() {
    TrackerConfig cfg;
    cfg.rear_camera = false;
    HazardTracker fwd(cfg);
    std::vector<CamDetection> approaching;
    for (int f = 0; f < 6; ++f) approaching.push_back(box(HazardType::SpeedBump, 0.5f, 0.55f + 0.04f * f, 0.1f + 0.02f * f));
    TEST_ASSERT_EQUAL(1, run(fwd, approaching, std::vector<bool>(6, true)).size());
    HazardTracker fwd2(cfg);
    TEST_ASSERT_EQUAL(0, run(fwd2, receding_bump(6), std::vector<bool>(6, true)).size());
}

void test_bump_outside_the_road_region_is_ignored() {
    // rear camera: the road is the lower part of the image; a box near the top (e.g. on a building) is ignored
    HazardTracker tr;
    std::vector<CamDetection> v;
    for (int f = 0; f < 6; ++f) v.push_back(box(HazardType::SpeedBump, 0.5f, 0.45f - 0.04f * f, 0.3f - 0.02f * f));
    TEST_ASSERT_EQUAL(0, run(tr, v, std::vector<bool>(6, true)).size());
}

void test_road_mask_overrides_the_region_when_available() {
    HazardTracker tr;
    auto v = receding_bump(6);
    for (auto& d : v) d.road_frac = 0.1f;  // a mask says: mostly not road (e.g. a pavement)
    TEST_ASSERT_EQUAL(0, run(tr, v, std::vector<bool>(6, true)).size());
}

void test_low_confidence_boxes_are_ignored() {
    HazardTracker tr;
    std::vector<CamDetection> v(5, box(HazardType::Pothole, 0.5f, 0.7f, 0.1f, 0.2f));
    TEST_ASSERT_EQUAL(0, run(tr, v, std::vector<bool>(5, true)).size());
}

void test_nothing_is_reported_while_parked() {
    HazardTracker tr;
    std::vector<CamDetection> v(5, box(HazardType::Pothole, 0.5f, 0.7f, 0.1f));
    TEST_ASSERT_EQUAL(0, run(tr, v, std::vector<bool>(5, true), 0.0f).size());
}

void test_two_separate_potholes_give_two_reports() {
    HazardTracker tr;
    CameraHazard out[4];
    size_t total = 0;
    for (int f = 0; f < 5; ++f) {
        CamDetection d[2] = {box(HazardType::Pothole, 0.2f, 0.7f, 0.1f), box(HazardType::Pothole, 0.8f, 0.7f, 0.1f)};
        total += tr.update(1000 + f * kFrameMs, 40, d, 2, out, 4);
    }
    TEST_ASSERT_EQUAL(2, total);
}

void test_a_gap_longer_than_allowed_starts_a_new_track() {
    HazardTracker tr;
    CameraHazard out[4];
    CamDetection d = box(HazardType::Pothole, 0.5f, 0.7f, 0.1f);
    tr.update(1000, 40, &d, 1, out, 4);
    tr.update(1125, 40, &d, 1, out, 4);
    tr.update(1250, 40, nullptr, 0, out, 4);
    tr.update(1800, 40, nullptr, 0, out, 4);  // 550 ms without a detection: the track closes
    TEST_ASSERT_EQUAL(0, tr.active_tracks());
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_receding_bump_is_confirmed_once);
    RUN_TEST(test_single_frame_flicker_is_not_reported);
    RUN_TEST(test_three_of_five_frames_confirms_a_pothole);
    RUN_TEST(test_a_confident_pothole_seen_in_two_frames_is_confirmed);
    RUN_TEST(test_two_frames_are_not_enough_for_a_speed_bump);
    RUN_TEST(test_static_bump_box_like_a_kerb_is_rejected);
    RUN_TEST(test_bump_moving_the_wrong_way_is_rejected);
    RUN_TEST(test_forward_camera_expects_the_opposite_motion);
    RUN_TEST(test_bump_outside_the_road_region_is_ignored);
    RUN_TEST(test_road_mask_overrides_the_region_when_available);
    RUN_TEST(test_low_confidence_boxes_are_ignored);
    RUN_TEST(test_nothing_is_reported_while_parked);
    RUN_TEST(test_two_separate_potholes_give_two_reports);
    RUN_TEST(test_a_gap_longer_than_allowed_starts_a_new_track);
    return UNITY_END();
}
