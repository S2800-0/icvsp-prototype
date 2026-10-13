#include "device.h"

#include <cstdio>
#include <cstring>

#include "signer.h"

namespace icvsp {

Device::Device(const char* device_id, uint32_t boot_id, uint8_t seed[32], Sink sink, void* ctx,
               TrackerConfig camera_cfg, FusionConfig fusion_cfg)
    : id_(device_id), sink_(sink), ctx_(ctx), camera_(camera_cfg), fusion_(fusion_cfg),
      builder_(device_id, "imu-v0", boot_id) {
    keypair_from_seed(seed, sk_, pk_);
}

void Device::remember(uint32_t t_ms, const GnssFix& fix) {
    if (history_n_ && t_ms - last_fix_ms_ < 100) return;
    last_fix_ms_ = t_ms;
    history_[history_n_ % kHistory] = fix;
    history_t_[history_n_ % kHistory] = t_ms;
    ++history_n_;
}

const GnssFix& Device::fix_at(uint32_t t_ms) const {
    size_t best = 0;
    uint32_t best_d = UINT32_MAX;
    for (size_t i = 0; i < (history_n_ < kHistory ? history_n_ : kHistory); ++i) {
        const uint32_t d = history_t_[i] > t_ms ? history_t_[i] - t_ms : t_ms - history_t_[i];
        if (d < best_d) { best_d = d; best = i; }
    }
    return history_[best];
}

void Device::on_imu(const ImuSample& s, const GnssFix& fix) {
    remember(s.t_ms, fix);
    Detection d;
    if (imu_detector_.update(s, fix.speed_kmh, d)) fusion_.add_imu(d);
    emit(s.t_ms, false);
}

void Device::on_frame(uint32_t t_ms, const CamDetection* dets, size_t n, const GnssFix& fix) {
    remember(t_ms, fix);
    CameraHazard found[4];
    const size_t m = camera_.update(t_ms, fix.speed_kmh, dets, n, found, 4);
    for (size_t i = 0; i < m; ++i) fusion_.add_camera(found[i]);
    emit(t_ms, false);
}

void Device::emit(uint32_t now_ms, bool flush) {
    FusedHazard ready[4];
    const size_t n = fusion_.poll(now_ms, ready, 4, flush);
    for (size_t i = 0; i < n; ++i) {
        const FusedHazard& f = ready[i];
        const Detection d{f.type, f.t_ms, f.imu_peak, 0, f.severity, f.confidence};
        const char* model = f.sensors == (kSensorImu | kSensorCamera) ? "imu+cam-v0"
                            : f.sensors == kSensorImu                ? "imu-v0"
                                                                     : "cam-v0";
        const GnssFix& fix = fix_at(f.t_ms);
        char event[640], msg_id[64];
        if (!builder_.build(d, fix, event, sizeof event, msg_id, sizeof msg_id, model)) continue;
        uint8_t sig[64];
        const MessageHeader h{id_, msg_id, fix.utc_ms};
        if (!sign_message(sk_, h, reinterpret_cast<const uint8_t*>(event), std::strlen(event), sig)) continue;
        char sig_hex[129], pk_hex[65], line[1100];
        to_hex(sig, 64, sig_hex);
        to_hex(pk_, 32, pk_hex);
        const int len = std::snprintf(line, sizeof line,
                                      "{\"event\":%s,\"sender\":\"%s\",\"msg_id\":\"%s\",\"utc_ms\":%lld,"
                                      "\"public_key\":\"%s\",\"signature\":\"%s\"}",
                                      event, id_, msg_id, static_cast<long long>(fix.utc_ms), pk_hex, sig_hex);
        if (len > 0 && static_cast<size_t>(len) < sizeof line) sink_(line, ctx_);
    }
}

}  // namespace icvsp
