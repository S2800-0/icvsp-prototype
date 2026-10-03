// ICVSP device: IMU hazard detection -> safety event -> Ed25519 signature.
//
// On a laptop (`pio run -e native`) this drives a simulated car over a road with a speed bump and a
// pothole and prints one signed report per line as JSON:
//     {"event": {...safety event...}, "sender": ..., "msg_id": ..., "utc_ms": ..., "public_key": ..., "signature": ...}
// tools/check_with_engine.py checks these lines with the trust engine's validation and an independent
// Ed25519 library.
//
// On the board the same loop will read the real IMU and GNSS; that part is a placeholder until we have one.
#include <cstdio>
#include <cstring>

#include "bump_detector.h"
#include "event_builder.h"
#include "signer.h"

namespace {

// Demo key only: a fixed, public seed. Real devices generate their seed once and keep it in
// encrypted flash; it must never be in source code.
const char* kDemoSeedHex = "4c4f43414c2d44454d4f2d4b45592d444f2d4e4f542d5553452d494e2d43415221";

struct Device {
    const char* id;
    uint8_t sk[64], pk[32];
    icvsp::BumpDetector detector;
    icvsp::EventBuilder builder;

    explicit Device(const char* device_id) : id(device_id), builder(device_id, "imu-v0") {
        uint8_t seed[32];
        icvsp::from_hex(kDemoSeedHex, seed, 32);
        icvsp::keypair_from_seed(seed, sk, pk);
    }

    // Feed one IMU sample; prints a signed report when a hazard is detected.
    void on_sample(const icvsp::ImuSample& s, const icvsp::GnssFix& fix) {
        icvsp::Detection d;
        if (!detector.update(s, fix.speed_kmh, d)) return;
        char event[512], msg_id[48];
        if (!builder.build(d, fix, event, sizeof event, msg_id, sizeof msg_id)) return;
        uint8_t sig[64];
        const icvsp::MessageHeader h{id, msg_id, fix.utc_ms};
        if (!icvsp::sign_message(sk, h, reinterpret_cast<const uint8_t*>(event), std::strlen(event), sig)) return;
        char sig_hex[129], pk_hex[65];
        icvsp::to_hex(sig, 64, sig_hex);
        icvsp::to_hex(pk, 32, pk_hex);
        std::printf("{\"event\":%s,\"sender\":\"%s\",\"msg_id\":\"%s\",\"utc_ms\":%lld,\"public_key\":\"%s\","
                    "\"signature\":\"%s\"}\n",
                    event, id, msg_id, static_cast<long long>(fix.utc_ms), pk_hex, sig_hex);
    }
};

}  // namespace

#ifdef ARDUINO
#include <Arduino.h>

void setup() { Serial.begin(115200); }
void loop() { delay(1000); }  // TODO: read the IMU and GNSS and call Device::on_sample

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
    Device dev("demo-car-01");
    for (const icvsp::ImuSample& s : drive.imu()) {
        const float t = s.t_ms / 1000.0f;
        dev.on_sample(s, gnss.at(t, drive.speed_at(t)));
    }
    return 0;
}
#endif
