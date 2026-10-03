#include "bump_detector.h"

#include <cmath>

namespace icvsp {

const char* to_string(HazardType t) { return t == HazardType::Pothole ? "pothole" : "speed_bump"; }

const char* to_string(Severity s) {
    switch (s) {
        case Severity::Low: return "low";
        case Severity::Medium: return "medium";
        default: return "high";
    }
}

float BumpDetector::vibration_rms() const { return std::sqrt(mean_sq_); }

float BumpDetector::threshold() const {
    const float t = cfg_.k_sigma * vibration_rms();
    return t > cfg_.min_jolt ? t : cfg_.min_jolt;
}

bool BumpDetector::update(const ImuSample& s, float speed_kmh, Detection& out) {
    if (!started_) {
        started_ = true;
        t0_ = last_t_ = s.t_ms;
        gravity_ = s.az;
        return false;
    }
    const float dt = (s.t_ms - last_t_) / 1000.0f;
    last_t_ = s.t_ms;
    if (dt <= 0) return false;

    // The trend follows slowly and is frozen during a jolt, so the jolt itself does not shift it.
    const float jolt = s.az - gravity_;
    if (!in_event_) gravity_ += (dt / (cfg_.gravity_tau_s + dt)) * jolt;

    const bool warm = s.t_ms - t0_ >= cfg_.warmup_ms;
    const bool moving = speed_kmh >= cfg_.min_speed_kmh;
    const float thr = threshold();
    const bool above = warm && std::fabs(jolt) > thr;

    if (!in_event_) {
        // vibration level is learnt only outside jolts
        mean_sq_ += (dt / (cfg_.rms_tau_s + dt)) * (jolt * jolt - mean_sq_);
        const bool refractory = has_ended_before_ && s.t_ms - ev_end_ < cfg_.refractory_ms;
        if (above && moving && !refractory) {
            in_event_ = true;
            ev_start_ = ev_last_above_ = s.t_ms;
            first_sign_ = jolt < 0 ? -1 : 1;
            ev_peak_ = std::fabs(jolt);
        }
        return false;
    }

    if (above) {
        ev_last_above_ = s.t_ms;
        if (std::fabs(jolt) > ev_peak_) ev_peak_ = std::fabs(jolt);
    }
    if (s.t_ms - ev_last_above_ < cfg_.quiet_ms) return false;

    // the jolt is over
    in_event_ = false;
    has_ended_before_ = true;
    ev_end_ = s.t_ms;
    out.t_ms = ev_start_;
    out.peak = ev_peak_;
    out.duration_ms = ev_last_above_ - ev_start_;
    out.type = (first_sign_ > 0 && out.duration_ms >= cfg_.bump_min_ms) ? HazardType::SpeedBump
                                                                         : HazardType::Pothole;
    out.severity = ev_peak_ >= cfg_.high_jolt     ? Severity::High
                   : ev_peak_ >= cfg_.medium_jolt ? Severity::Medium
                                                  : Severity::Low;
    // 0.5 at the threshold, rising towards 0.95 for jolts far above it
    const float ratio = ev_peak_ / thr;
    float c = 0.5f + 0.15f * (ratio - 1.0f);
    out.confidence = c < 0.5f ? 0.5f : (c > 0.95f ? 0.95f : c);
    return true;
}

}  // namespace icvsp
