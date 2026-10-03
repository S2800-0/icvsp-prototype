#pragma once
#include "types.h"

namespace icvsp {

// Detects potholes and speed bumps from the vertical acceleration (proposed FR-27).
//
// Gravity and slow changes (slopes, braking pitch) are removed with a slow running average; what is left
// is the vertical jolt. A jolt counts as a hazard when it is several times larger than the car's usual
// vibration on that road (a running RMS), and at least `min_jolt`, while the car is moving.
//
// Type, from the shape of the jolt:
//   - the wheel drops first (downward first)            -> pothole
//   - lifted first and lasting at least `bump_min_ms`    -> speed bump
//   - lifted first but short (an edge or a crack)        -> pothole
// All thresholds are first guesses and must be tuned on real recordings.
struct BumpConfig {
    float gravity_tau_s = 1.0f;     // time constant of the gravity / slow-trend average
    float rms_tau_s = 3.0f;         // time constant of the vibration level
    float k_sigma = 5.0f;           // jolt must exceed k_sigma x vibration RMS ...
    float min_jolt = 2.0f;          // ... and at least this, m/s^2
    float min_speed_kmh = 8.0f;     // ignore jolts when (nearly) stopped: doors, people getting in
    uint32_t warmup_ms = 1000;      // learn the vibration level first
    uint32_t quiet_ms = 150;        // jolt is over after this long below threshold
    uint32_t bump_min_ms = 200;
    uint32_t refractory_ms = 1000;  // one report per hazard (front and rear wheels)
    float medium_jolt = 4.0f;       // severity bands on the peak
    float high_jolt = 8.0f;
};

class BumpDetector {
public:
    explicit BumpDetector(BumpConfig cfg = {}) : cfg_(cfg) {}

    // Feed one sample. Returns true and fills `out` when a hazard has just ended.
    bool update(const ImuSample& s, float speed_kmh, Detection& out);

    float vibration_rms() const;
    float threshold() const;

private:
    BumpConfig cfg_;
    bool started_ = false;
    uint32_t t0_ = 0, last_t_ = 0;
    float gravity_ = 0, mean_sq_ = 0;

    bool in_event_ = false;
    uint32_t ev_start_ = 0, ev_last_above_ = 0, ev_end_ = 0;
    int first_sign_ = 0;
    float ev_peak_ = 0;
    bool has_ended_before_ = false;
};

}  // namespace icvsp
