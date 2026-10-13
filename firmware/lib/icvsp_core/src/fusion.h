#pragma once
#include <cstddef>
#include <cstdint>

#include "hazard_tracker.h"
#include "types.h"

namespace icvsp {

// Combines IMU detections (the car felt it) and confirmed camera hazards (the camera saw it) into one
// report per hazard.
//
// A camera hazard and an IMU detection are the same hazard when they fall within the matching window:
// with a rear camera the camera sees the hazard 0..`after_ms` after the wheels hit it; with a forward
// camera it sees it 0..`before_ms` before. A matched pair becomes one report whose confidence combines
// both (1 - (1 - c_imu)(1 - c_cam)). If the two disagree on the type, the IMU decides, because it
// measured the physical impact. Unmatched detections are reported on their own once the window has
// passed, with their own confidence, so the trust engine can weigh single-sensor reports lower.
//
// Reports keep the device time of the IMU impact when there is one (the position of the hazard), else
// the camera time.

enum SensorBits : uint8_t { kSensorImu = 1, kSensorCamera = 2 };

struct FusedHazard {
    HazardType type;
    uint32_t t_ms;
    float confidence;
    Severity severity;   // from the IMU; Low for camera-only reports
    uint8_t sensors;     // SensorBits
    float imu_peak;      // largest vertical jolt felt by the IMU, m/s^2; 0 for camera-only reports
};

struct FusionConfig {
    bool rear_camera = true;
    uint32_t after_ms = 3000;   // rear camera: camera confirms up to 3 s after the impact
    uint32_t before_ms = 4000;  // forward camera: camera confirms up to 4 s before the impact
};

class Fusion {
public:
    static constexpr size_t kMaxPending = 8;

    explicit Fusion(FusionConfig cfg = {}) : cfg_(cfg) {}

    void add_imu(const Detection& d);
    void add_camera(const CameraHazard& c);

    // Writes reports whose matching window has closed (or everything when flush is true).
    size_t poll(uint32_t now_ms, FusedHazard* out, size_t cap, bool flush = false);

private:
    struct Pending {
        bool used;
        uint8_t sensor;
        uint32_t t_ms;
        HazardType type;
        float conf;
        Severity severity;
        float peak;
    };
    bool in_window(uint32_t t_imu, uint32_t t_cam) const;
    void push(const Pending& p);

    FusionConfig cfg_;
    Pending pending_[kMaxPending] = {};
};

}  // namespace icvsp
