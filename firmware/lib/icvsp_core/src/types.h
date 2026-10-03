#pragma once
#include <cstdint>

namespace icvsp {

// One IMU reading. Accelerations in m/s^2 in the vehicle frame: x forward, y left, z up
// (z includes gravity, about +9.81 when level).
struct ImuSample {
    uint32_t t_ms;
    float ax, ay, az;
};

// One GNSS fix. utc_ms is Unix time in milliseconds, taken from GNSS so that every device agrees on
// the clock used for freshness checks.
struct GnssFix {
    bool valid;
    double lat, lon;
    float acc_m;
    float speed_kmh;
    float heading_deg;
    int64_t utc_ms;
};

enum class HazardType { Pothole, SpeedBump };
enum class Severity { Low, Medium, High };

const char* to_string(HazardType t);
const char* to_string(Severity s);

// A hazard felt by the IMU, before position and time are attached.
struct Detection {
    HazardType type;
    uint32_t t_ms;        // start of the jolt, device clock
    float peak;           // largest vertical acceleration change, m/s^2
    uint32_t duration_ms;
    Severity severity;
    float confidence;     // 0..1
};

}  // namespace icvsp
