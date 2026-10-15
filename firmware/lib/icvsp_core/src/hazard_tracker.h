#pragma once
#include <cstddef>
#include <cstdint>

#include "types.h"

namespace icvsp {

// Turns per-frame camera detections into confirmed hazards (the decision logic after the detector).
//
// The detector draws boxes in single frames, and many of them are wrong: kerbs, reflections, buses.
// A box only becomes a hazard report when
//   1. its confidence is at least `min_conf`;
//   2. for speed bumps, it lies on the road: either the road fraction measured around the box is at least
//      `min_road_frac` (when a road mask is available), or its centre lies in the road region of the
//      image (below `road_top` of the height; the road is at the bottom for forward and rear cameras);
//   3. it is linked into a track (same class, centre moved at most `max_jump`, gap at most `max_gap_ms`)
//      that was detected in at least `k_hits` of the last `n_frames` processed frames (potholes: also
//      `pothole_k_hits` frames when the best box reaches `pothole_min_peak_conf`);
//   4. for speed bumps, the track moves like an object fixed to the road (motion check): with a forward
//      camera it moves down the image and does not shrink as the car approaches; with a rear camera it
//      moves up and does not grow as the car drives away. Moved at least `min_drop` image heights in
//      total, at at least `min_rate` image heights per second (least-squares slope);
//   5. the car is moving (at least `min_speed_kmh`).
// Each track is reported once. The motion check and the road check apply to speed bumps only: they
// were tested on bumps, and pothole false alarms lie on the road and barely move differently.
// Thresholds are the ones fixed before scoring in ai/scripts/motion_check.py and road_filter.py.

struct CamDetection {
    HazardType type;
    float conf;
    float x1, y1, x2, y2;     // box, fractions of the image (0..1), y down
    float road_frac = -1.0f;  // road share around the box from a road mask; < 0 if not available
};

struct CameraHazard {
    HazardType type;
    uint32_t t_first_ms;  // first detection of the track
    uint32_t t_ms;        // when it was confirmed
    float conf;           // highest detection confidence in the track
    uint16_t hits;
    float cx, cy;         // box centre at confirmation
};

struct TrackerConfig {
    bool rear_camera = true;      // the prototype camera faces backwards
    float min_conf = 0.25f;
    float min_road_frac = 0.30f;  // lenient rule from road_filter.py
    float road_top = 0.40f;       // road is below 40 % of the image height (crop test)
    uint32_t max_gap_ms = 400;
    float max_jump = 0.12f;
    uint8_t k_hits = 3;
    uint8_t n_frames = 5;
    // Potholes pass a forward camera in about 2 processed frames, so a pothole track is also confirmed with
    // pothole_k_hits of the last n_frames when its best box reaches pothole_min_peak_conf (0 = off). Chosen on
    // the held-out Cairo night video and checked on the day video (firmware/results/pothole_rule.md);
    // provisional until the team's own recordings.
    uint8_t pothole_k_hits = 2;
    float pothole_min_peak_conf = 0.40f;
    float min_drop = 0.03f;
    float min_rate = 0.02f;       // image heights per second
    float min_speed_kmh = 8.0f;
};

class HazardTracker {
public:
    static constexpr size_t kMaxTracks = 16;
    static constexpr size_t kMaxPoints = 32;

    explicit HazardTracker(const TrackerConfig& cfg = {}) : cfg_(cfg) {}

    // Feed one processed frame (also frames without detections). Writes up to `cap` newly confirmed
    // hazards to `out` and returns how many.
    size_t update(uint32_t t_ms, float speed_kmh, const CamDetection* dets, size_t n, CameraHazard* out,
                  size_t cap);

    size_t active_tracks() const;

private:
    struct Point { uint32_t t_ms; float cy, w; };
    struct Track {
        bool used;
        bool reported;
        HazardType type;
        float cx, cy;            // last centre
        float max_conf;
        uint32_t t_first_ms, t_last_ms;
        uint32_t hit_frames;     // bit i set = detected i frames ago
        uint16_t hits;
        uint8_t n_points;
        Point points[kMaxPoints];
    };

    bool on_road(const CamDetection& d) const;
    bool moves_like_fixed_object(const Track& tr) const;
    bool confirmed(const Track& tr) const;

    TrackerConfig cfg_;
    Track tracks_[kMaxTracks] = {};
};

}  // namespace icvsp
