#pragma once
#include <cstddef>
#include <cstdint>

#include "bump_detector.h"
#include "event_builder.h"
#include "fusion.h"
#include "hazard_tracker.h"

namespace icvsp {

// The whole device pipeline: IMU detector and camera decision logic -> fusion -> safety event (format v2)
// -> Ed25519 signature. The same class runs on the board, in the native demo and in the system simulation;
// only the source of sensor data and the destination of the signed reports differ.
//
// Each signed report is handed to `sink` as one line of JSON:
//     {"event": {...}, "sender": ..., "msg_id": ..., "utc_ms": ..., "public_key": ..., "signature": ...}
// The event bytes inside it are exactly the signed bytes, so receivers must forward the line unchanged.
class Device {
public:
    using Sink = void (*)(const char* line, void* ctx);

    // seed: the device's 32-byte private seed (wiped after use); boot_id must grow at every start.
    Device(const char* device_id, uint32_t boot_id, uint8_t seed[32], Sink sink, void* ctx,
           TrackerConfig camera_cfg = {}, FusionConfig fusion_cfg = {});

    void on_imu(const ImuSample& s, const GnssFix& fix);                                // ~100 Hz
    void on_frame(uint32_t t_ms, const CamDetection* dets, size_t n, const GnssFix& fix);  // each camera frame
    void flush(uint32_t now_ms) { emit(now_ms, true); }                                  // e.g. at shutdown

    const uint8_t* public_key() const { return pk_; }

private:
    void remember(uint32_t t_ms, const GnssFix& fix);
    const GnssFix& fix_at(uint32_t t_ms) const;
    void emit(uint32_t now_ms, bool flush);

    static constexpr size_t kHistory = 80;  // one fix per 100 ms: 8 s, longer than the fusion window
    const char* id_;
    uint8_t sk_[64], pk_[32];
    Sink sink_;
    void* ctx_;
    BumpDetector imu_detector_;
    HazardTracker camera_;
    Fusion fusion_;
    EventBuilder builder_;
    GnssFix history_[kHistory] = {};
    uint32_t history_t_[kHistory] = {};
    size_t history_n_ = 0;
    uint32_t last_fix_ms_ = 0;
};

}  // namespace icvsp
