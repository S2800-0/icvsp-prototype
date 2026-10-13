// ICVSP device: IMU hazard detection -> safety event -> Ed25519 signature.
//
// On a laptop (`pio run -e native`) this drives a simulated car over a road with a speed bump and a
// pothole and prints one signed report per line as JSON:
//     {"event": {...safety event...}, "sender": ..., "msg_id": ..., "utc_ms": ..., "public_key": ..., "signature": ...}
// tools/check_with_engine.py checks these lines with the trust engine's validation and an independent
// Ed25519 library.
//
// On the board the same loop will read the real IMU and GNSS; that part is a placeholder until we have one.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "bump_detector.h"
#include "fusion.h"
#include "hazard_tracker.h"
#include "event_builder.h"
#include "signer.h"

namespace {

// Demo key only: a fixed, public seed. Real devices generate their seed once and keep it in
// encrypted flash; it must never be in source code.
const char* kDemoSeedHex = "4c4f43414c2d44454d4f2d4b45592d444f2d4e4f542d5553452d494e2d434152";  // "LOCAL-DEMO-KEY-DO-NOT-USE-IN-CAR"

struct Device {
    const char* id;
    uint8_t sk[64], pk[32];
    icvsp::BumpDetector imu_detector;
    icvsp::HazardTracker camera;  // decision logic after the camera detector (rear camera)
    icvsp::Fusion fusion;         // one report per hazard from IMU and camera
    icvsp::EventBuilder builder;
    // recent GNSS fixes, so a report sent after the fusion window carries the position of the impact
    icvsp::GnssFix history[80] = {};
    uint32_t history_t[80] = {};
    size_t history_n = 0;
    uint32_t last_fix_ms = 0;

    // boot_id must grow at every start; on the board it will be a counter kept in flash (NVS)
    Device(const char* device_id, uint32_t boot_id) : id(device_id), builder(device_id, "imu-v0", boot_id) {
        uint8_t seed[32];
        if (!icvsp::from_hex(kDemoSeedHex, seed, 32)) {  // never sign with an uninitialised key
            std::fprintf(stderr, "bad device key\n");
            std::abort();
        }
        icvsp::keypair_from_seed(seed, sk, pk);
    }

    void remember(uint32_t t_ms, const icvsp::GnssFix& fix) {
        if (history_n && t_ms - last_fix_ms < 100) return;  // keep one fix per 100 ms (8 s of history)
        last_fix_ms = t_ms;
        history[history_n % 80] = fix;
        history_t[history_n % 80] = t_ms;
        ++history_n;
    }

    const icvsp::GnssFix& fix_at(uint32_t t_ms) const {
        size_t best = 0;
        uint32_t best_d = UINT32_MAX;
        for (size_t i = 0; i < (history_n < 80 ? history_n : 80); ++i) {
            const uint32_t d = history_t[i] > t_ms ? history_t[i] - t_ms : t_ms - history_t[i];
            if (d < best_d) { best_d = d; best = i; }
        }
        return history[best];
    }

    // One IMU sample (100 Hz).
    void on_imu(const icvsp::ImuSample& s, const icvsp::GnssFix& fix) {
        remember(s.t_ms, fix);
        icvsp::Detection d;
        if (imu_detector.update(s, fix.speed_kmh, d)) fusion.add_imu(d);
        emit(s.t_ms, false);
    }

    // One camera frame with the detector's boxes (about 8 per second on the ESP32-S3).
    void on_frame(uint32_t t_ms, const icvsp::CamDetection* dets, size_t n, const icvsp::GnssFix& fix) {
        remember(t_ms, fix);
        icvsp::CameraHazard found[4];
        const size_t m = camera.update(t_ms, fix.speed_kmh, dets, n, found, 4);
        for (size_t i = 0; i < m; ++i) fusion.add_camera(found[i]);
        emit(t_ms, false);
    }

    // Builds, signs and prints every report whose fusion window has closed.
    void emit(uint32_t now_ms, bool flush) {
        icvsp::FusedHazard ready[4];
        const size_t n = fusion.poll(now_ms, ready, 4, flush);
        for (size_t i = 0; i < n; ++i) {
            const icvsp::FusedHazard& f = ready[i];
            const icvsp::Detection d{f.type, f.t_ms, f.imu_peak, 0, f.severity, f.confidence};
            const char* model = f.sensors == (icvsp::kSensorImu | icvsp::kSensorCamera) ? "imu+cam-v0"
                                : f.sensors == icvsp::kSensorImu                         ? "imu-v0"
                                                                                         : "cam-v0";
            const icvsp::GnssFix& fix = fix_at(f.t_ms);
            char event[640], msg_id[64];
            if (!builder.build(d, fix, event, sizeof event, msg_id, sizeof msg_id, model)) continue;
            uint8_t sig[64];
            const icvsp::MessageHeader h{id, msg_id, fix.utc_ms};
            if (!icvsp::sign_message(sk, h, reinterpret_cast<const uint8_t*>(event), std::strlen(event), sig)) continue;
            char sig_hex[129], pk_hex[65];
            icvsp::to_hex(sig, 64, sig_hex);
            icvsp::to_hex(pk, 32, pk_hex);
            std::printf("{\"event\":%s,\"sender\":\"%s\",\"msg_id\":\"%s\",\"utc_ms\":%lld,\"public_key\":\"%s\","
                        "\"signature\":\"%s\"}\n",
                        event, id, msg_id, static_cast<long long>(fix.utc_ms), pk_hex, sig_hex);
        }
    }
};

}  // namespace

#ifdef ARDUINO
#include <Arduino.h>

void setup() { Serial.begin(115200); }
void loop() { delay(1000); }  // TODO: read the IMU, camera detector and GNSS and call Device::on_imu / on_frame

#else
#include "sim.h"

int main() {
    using icvsp::sim::Feature;
    icvsp::sim::Drive drive;
    drive.duration_s = 30;
    drive.speed_kmh = 35;
    drive.events = {{Feature::SpeedBump, 6.0f, 4.0f}, {Feature::Braking, 12.0f, 3.0f},
                    {Feature::Pothole, 20.0f, 9.0f}};
    icvsp::sim::FakeGnss gnss;
    Device dev("demo-car-01", 1);
    for (const icvsp::ImuSample& s : drive.imu()) {
        const float t = s.t_ms / 1000.0f;
        const icvsp::GnssFix fix = gnss.at(t, drive.speed_at(t));
        dev.on_imu(s, fix);
        if (s.t_ms % 125 == 0) {  // rear camera frame, 8 per second
            icvsp::CamDetection d[2];
            size_t n = 0;
            if (t >= 20.1f && t <= 21.0f) {  // the pothole, seen moving up and shrinking after the car passed it
                const float u = t - 20.1f, cy = 0.85f - 0.25f * u, w = 0.20f - 0.10f * u;
                d[n++] = {icvsp::HazardType::Pothole, 0.6f, 0.5f - w / 2, cy - 0.02f, 0.5f + w / 2, cy + 0.02f, -1.0f};
            }
            if (t >= 9.0f && t <= 11.0f)  // a kerb boxed as a "speed bump" at the same place every frame
                d[n++] = {icvsp::HazardType::SpeedBump, 0.5f, 0.05f, 0.70f, 0.25f, 0.74f, -1.0f};
            dev.on_frame(s.t_ms, d, n, fix);
        }
    }
    dev.emit(UINT32_MAX, true);
    return 0;
}
#endif
