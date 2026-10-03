#pragma once
// Simulated hardware for laptop tests and the native demo: an IMU on a car driving over a road with
// chosen hazards, and a GNSS receiver following the car. Signals are synthetic (shapes and sizes are
// rough guesses), deterministic for a given seed.
#include <cmath>
#include <cstdint>
#include <vector>

#include "types.h"

namespace icvsp::sim {

enum class Feature { SpeedBump, Pothole, Braking, DoorSlam };

struct Event {
    Feature kind;
    float t_s;        // when it starts
    float size;       // peak vertical jolt (m/s^2), or deceleration for braking
};

class Rng {
public:
    explicit Rng(uint32_t seed) : s_(seed ? seed : 1) {}
    float uniform() {  // xorshift32, [0, 1)
        s_ ^= s_ << 13; s_ ^= s_ >> 17; s_ ^= s_ << 5;
        return (s_ >> 8) * (1.0f / 16777216.0f);
    }
    float gauss() {  // Box-Muller
        const float u1 = uniform() + 1e-7f, u2 = uniform();
        return std::sqrt(-2.0f * std::log(u1)) * std::cos(6.2831853f * u2);
    }
private:
    uint32_t s_;
};

struct Drive {
    float rate_hz = 100.0f;
    float duration_s = 20.0f;
    float speed_kmh = 40.0f;
    float noise = 0.3f;          // road vibration, RMS m/s^2
    uint32_t seed = 1;
    std::vector<Event> events;

    // speed at time t (braking slows the car)
    float speed_at(float t) const {
        float v = speed_kmh;
        for (const Event& e : events)
            if (e.kind == Feature::Braking && t > e.t_s) v -= 3.6f * e.size * std::fmin(t - e.t_s, 2.0f);
        return v > 0 ? v : 0;
    }

    std::vector<ImuSample> imu() const {
        const float kPi = 3.14159265f;
        Rng rng(seed);
        std::vector<ImuSample> out;
        const int n = static_cast<int>(duration_s * rate_hz);
        for (int i = 0; i < n; ++i) {
            const float t = i / rate_hz;
            float az = 9.81f + noise * rng.gauss(), ax = 0.1f * rng.gauss();
            for (const Event& e : events) {
                const float u = t - e.t_s;
                switch (e.kind) {
                    case Feature::SpeedBump:  // front wheels lift for ~0.25 s, then drop for ~0.25 s
                        if (u >= 0 && u < 0.5f) az += e.size * std::sin(2 * kPi * u / 0.5f);
                        break;
                    case Feature::Pothole:    // wheel drops for ~60 ms, then hits the far edge
                        if (u >= 0 && u < 0.06f) az -= 0.6f * e.size * std::sin(kPi * u / 0.06f);
                        else if (u >= 0.06f && u < 0.12f) az += e.size * std::sin(kPi * (u - 0.06f) / 0.06f);
                        break;
                    case Feature::Braking:    // 2 s of deceleration; the nose dips a little (slow change)
                        if (u >= 0 && u < 2.0f) {
                            ax -= e.size;
                            az -= 0.4f * std::sin(kPi * u / 2.0f);
                        }
                        break;
                    case Feature::DoorSlam:   // sharp knock while parked
                        if (u >= 0 && u < 0.05f) az += e.size * std::sin(kPi * u / 0.05f);
                        break;
                }
            }
            out.push_back({static_cast<uint32_t>(std::lround(t * 1000)), ax, 0.1f * rng.gauss(), az});
        }
        return out;
    }
};

// A GNSS receiver on a car driving in a straight line.
struct FakeGnss {
    double lat0 = 30.0444, lon0 = 31.2357;   // Cairo
    float heading_deg = 90.0f;               // east
    float acc_m = 3.5f;
    int64_t utc0_ms = 1790000000000;         // 2026-09-21
    bool valid = true;

    GnssFix at(float t_s, float speed_kmh) const {
        const double dist = speed_kmh / 3.6 * t_s;  // constant-speed approximation
        const double rad = heading_deg * 3.14159265358979 / 180.0;
        const double north = dist * std::cos(rad), east = dist * std::sin(rad);
        GnssFix f;
        f.valid = valid;
        f.lat = lat0 + north / 111320.0;
        f.lon = lon0 + east / (111320.0 * std::cos(lat0 * 3.14159265358979 / 180.0));
        f.acc_m = acc_m;
        f.speed_kmh = speed_kmh;
        f.heading_deg = heading_deg;
        f.utc_ms = utc0_ms + static_cast<int64_t>(t_s * 1000);
        return f;
    }
};

}  // namespace icvsp::sim
