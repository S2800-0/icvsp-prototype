#include "fusion.h"

namespace icvsp {

bool Fusion::in_window(uint32_t t_imu, uint32_t t_cam) const {
    if (cfg_.rear_camera) return t_cam >= t_imu && t_cam - t_imu <= cfg_.after_ms;
    return t_cam <= t_imu && t_imu - t_cam <= cfg_.before_ms;
}

void Fusion::push(const Pending& p) {
    for (Pending& q : pending_)
        if (!q.used) { q = p; q.used = true; return; }
    // full: replace the oldest (it would have been reported on the next poll anyway)
    Pending* oldest = &pending_[0];
    for (Pending& q : pending_)
        if (q.t_ms < oldest->t_ms) oldest = &q;
    *oldest = p;
    oldest->used = true;
}

void Fusion::add_imu(const Detection& d) {
    push(Pending{true, kSensorImu, d.t_ms, d.type, d.confidence, d.severity, d.peak});
}

void Fusion::add_camera(const CameraHazard& c) {
    push(Pending{true, kSensorCamera, c.t_ms, c.type, c.conf, Severity::Low, 0.0f});
}

size_t Fusion::poll(uint32_t now_ms, FusedHazard* out, size_t cap, bool flush) {
    size_t n = 0;
    const uint32_t wait = cfg_.rear_camera ? cfg_.after_ms : cfg_.before_ms;
    for (Pending& a : pending_) {
        if (!a.used || n >= cap) continue;
        // pair each IMU detection with the closest camera hazard in its window
        if (a.sensor == kSensorImu) {
            Pending* best = nullptr;
            for (Pending& b : pending_) {
                if (!b.used || b.sensor != kSensorCamera || !in_window(a.t_ms, b.t_ms)) continue;
                const uint32_t da = b.t_ms > a.t_ms ? b.t_ms - a.t_ms : a.t_ms - b.t_ms;
                const uint32_t db = best ? (best->t_ms > a.t_ms ? best->t_ms - a.t_ms : a.t_ms - best->t_ms) : 0;
                if (!best || da < db) best = &b;
            }
            if (best) {
                out[n++] = FusedHazard{a.type, a.t_ms, 1.0f - (1.0f - a.conf) * (1.0f - best->conf), a.severity,
                                       static_cast<uint8_t>(kSensorImu | kSensorCamera), a.peak};
                a.used = best->used = false;
                continue;
            }
        }
    }
    // single-sensor reports once no partner can arrive any more
    for (Pending& a : pending_) {
        if (!a.used || n >= cap) continue;
        bool expired;
        if (a.sensor == kSensorImu)
            expired = cfg_.rear_camera ? now_ms - a.t_ms > wait : true;  // forward camera came first
        else
            expired = cfg_.rear_camera ? true : now_ms - a.t_ms > wait;  // rear camera came last
        if (!expired && !flush) continue;
        out[n++] = FusedHazard{a.type, a.t_ms, a.conf, a.severity, a.sensor, a.peak};
        a.used = false;
    }
    return n;
}

}  // namespace icvsp
