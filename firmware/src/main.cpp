// ICVSP device: IMU and camera hazard detection -> safety event -> Ed25519 signature (icvsp::Device).
//
// On a laptop (`pio run -e native`) this drives a simulated car over a road with a speed bump and a
// pothole and prints one signed report per line as JSON:
//     {"event": {...safety event...}, "sender": ..., "msg_id": ..., "utc_ms": ..., "public_key": ..., "signature": ...}
// tools/check_with_engine.py checks these lines with the trust engine's validation and an independent
// Ed25519 library. The system simulation (sil/) runs the same Device class for many vehicles.
//
// On the board the same loop will read the real IMU and GNSS; that part is a placeholder until we have one.
#include <cstdint>
#include <cstdio>
#include <cstdlib>

#include "device.h"
#include "signer.h"

namespace {

// Demo key only: a fixed, public seed. Real devices generate their seed once and keep it in
// encrypted flash; it must never be in source code.
const char* kDemoSeedHex = "4c4f43414c2d44454d4f2d4b45592d444f2d4e4f542d5553452d494e2d434152";  // "LOCAL-DEMO-KEY-DO-NOT-USE-IN-CAR"

void print_line(const char* line, void*) { std::printf("%s\n", line); }

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
    uint8_t seed[32];
    if (!icvsp::from_hex(kDemoSeedHex, seed, 32)) {  // never sign with an uninitialised key
        std::fprintf(stderr, "bad device key\n");
        return 1;
    }
    // boot_id must grow at every start; on the board it will be a counter kept in flash (NVS)
    icvsp::Device dev("demo-car-01", 1, seed, print_line, nullptr);
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
    dev.flush(UINT32_MAX);
    return 0;
}
#endif
