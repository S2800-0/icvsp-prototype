// One simulated vehicle for the system simulation (sil/): the real device code (icvsp::Device) driven by a
// simulated IMU, GNSS and rear-camera detector output. Prints one line per signed report (format v2):
//     <device time of emission, ms> TAB <signed report line>
//
// The scenario comes on stdin, one directive per line (times in seconds from the start of the drive):
//     id car-07                 device ID              boot 3            boot counter
//     seed <64 hex chars>       private key seed       start <utc ms>    GNSS time at t = 0
//     origin <lat> <lon> <heading_deg>                 speed <km/h>      duration <s>
//     sim_seed <n>              noise <m/s^2 RMS>
//     imu pothole|speed_bump|braking <t> <size>        the wheels hit it (or the car brakes) at t
//     cam pothole|speed_bump <t> <conf>                the rear camera sees it receding from t for 0.9 s
//     falsecam pothole|speed_bump <t> <conf>           a static box (kerb, shadow) from t for 2 s
//     gpsloss <t0> <t1>                                no position fix between t0 and t1
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "device.h"
#include "signer.h"
#include "sim.h"

using namespace icvsp;

namespace {

struct Cam { HazardType type; float t, conf; bool fake; };

HazardType hazard(const char* s) { return std::strcmp(s, "pothole") == 0 ? HazardType::Pothole : HazardType::SpeedBump; }

uint32_t g_now_ms = 0;  // device time of the sample being processed, i.e. when a report is emitted

void print_line(const char* line, void*) { std::printf("%u\t%s\n", g_now_ms, line); }

}  // namespace

int main() {
    std::string id = "sim-car", seed_hex;
    unsigned boot = 1;
    float gps_off0 = -1, gps_off1 = -1;
    sim::Drive drive;
    sim::FakeGnss gnss;
    std::vector<Cam> cams;

    char line[512];
    while (std::fgets(line, sizeof line, stdin)) {
        char key[32] = "", a[128] = "";
        double x = 0, y = 0, z = 0;
        if (std::sscanf(line, "%31s", key) != 1 || key[0] == '#') continue;
        const char* rest = line + std::strlen(key);
        if (!std::strcmp(key, "id")) { std::sscanf(rest, "%127s", a); id = a; }
        else if (!std::strcmp(key, "boot")) std::sscanf(rest, "%u", &boot);
        else if (!std::strcmp(key, "seed")) { std::sscanf(rest, "%127s", a); seed_hex = a; }
        else if (!std::strcmp(key, "start")) { long long ms; std::sscanf(rest, "%lld", &ms); gnss.utc0_ms = ms; }
        else if (!std::strcmp(key, "origin")) { std::sscanf(rest, "%lf %lf %lf", &x, &y, &z); gnss.lat0 = x; gnss.lon0 = y; gnss.heading_deg = static_cast<float>(z); }
        else if (!std::strcmp(key, "speed")) { std::sscanf(rest, "%lf", &x); drive.speed_kmh = static_cast<float>(x); }
        else if (!std::strcmp(key, "duration")) { std::sscanf(rest, "%lf", &x); drive.duration_s = static_cast<float>(x); }
        else if (!std::strcmp(key, "sim_seed")) { unsigned s; std::sscanf(rest, "%u", &s); drive.seed = s; }
        else if (!std::strcmp(key, "noise")) { std::sscanf(rest, "%lf", &x); drive.noise = static_cast<float>(x); }
        else if (!std::strcmp(key, "gpsloss")) { std::sscanf(rest, "%lf %lf", &x, &y); gps_off0 = static_cast<float>(x); gps_off1 = static_cast<float>(y); }
        else if (!std::strcmp(key, "imu")) {
            std::sscanf(rest, "%127s %lf %lf", a, &x, &y);
            const sim::Feature f = !std::strcmp(a, "pothole") ? sim::Feature::Pothole
                                   : !std::strcmp(a, "braking") ? sim::Feature::Braking : sim::Feature::SpeedBump;
            drive.events.push_back({f, static_cast<float>(x), static_cast<float>(y)});
        } else if (!std::strcmp(key, "cam") || !std::strcmp(key, "falsecam")) {
            std::sscanf(rest, "%127s %lf %lf", a, &x, &y);
            cams.push_back({hazard(a), static_cast<float>(x), static_cast<float>(y), key[0] == 'f'});
        } else {
            std::fprintf(stderr, "unknown directive: %s\n", key);
            return 2;
        }
    }
    uint8_t seed[32];
    if (!from_hex(seed_hex.c_str(), seed, 32)) {
        std::fprintf(stderr, "seed must be 64 hex characters\n");
        return 2;
    }
    Device dev(id.c_str(), boot, seed, print_line, nullptr);

    for (const ImuSample& s : drive.imu()) {
        const float t = s.t_ms / 1000.0f;
        g_now_ms = s.t_ms;
        GnssFix fix = gnss.at(t, drive.speed_at(t));
        if (t >= gps_off0 && t < gps_off1) fix.valid = false;
        dev.on_imu(s, fix);
        if (s.t_ms % 125 != 0) continue;  // rear camera frames, 8 per second
        CamDetection d[8];
        size_t n = 0;
        for (const Cam& c : cams) {
            const float u = t - c.t;
            if (n == 8) break;
            if (!c.fake && u >= 0 && u <= 0.9f) {  // receding: moves up the image and shrinks
                const float cy = 0.85f - 0.25f * u, w = 0.20f - 0.10f * u;
                d[n++] = {c.type, c.conf, 0.5f - w / 2, cy - 0.02f, 0.5f + w / 2, cy + 0.02f, -1.0f};
            } else if (c.fake && u >= 0 && u <= 2.0f) {  // fixed in the image: travels with the car
                d[n++] = {c.type, c.conf, 0.05f, 0.70f, 0.25f, 0.74f, -1.0f};
            }
        }
        dev.on_frame(s.t_ms, d, n, fix);
    }
    g_now_ms = static_cast<uint32_t>(drive.duration_s * 1000);
    dev.flush(UINT32_MAX);
    return 0;
}
