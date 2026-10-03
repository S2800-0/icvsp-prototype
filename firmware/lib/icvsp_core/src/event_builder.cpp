#include "event_builder.h"

#include <cmath>
#include <cstdio>

namespace icvsp {

void format_utc(int64_t utc_ms, char out[25]) {
    int64_t secs = utc_ms >= 0 ? utc_ms / 1000 : (utc_ms - 999) / 1000;
    const int ms = static_cast<int>(utc_ms - secs * 1000);
    int64_t days = secs >= 0 ? secs / 86400 : (secs - 86399) / 86400;
    const int sod = static_cast<int>(secs - days * 86400);
    // civil date from days since 1970-01-01 (H. Hinnant's algorithm), no libc time functions needed
    days += 719468;
    const int64_t era = (days >= 0 ? days : days - 146096) / 146097;
    const unsigned doe = static_cast<unsigned>(days - era * 146097);
    const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const unsigned mp = (5 * doy + 2) / 153;
    const unsigned day = doy - (153 * mp + 2) / 5 + 1;
    const unsigned month = mp < 10 ? mp + 3 : mp - 9;
    const long long year = static_cast<long long>(yoe) + era * 400 + (month <= 2);
    std::snprintf(out, 25, "%04lld-%02u-%02uT%02d:%02d:%02d.%03dZ", year, month, day, sod / 3600,
                  (sod / 60) % 60, sod % 60, ms);
}

void grid_cell(double lat, double lon, char* out, size_t cap) {
    const long gy = static_cast<long>(std::floor(lat * 500.0));  // 1/500 degree, about 220 m
    const long gx = static_cast<long>(std::floor(lon * 500.0));
    std::snprintf(out, cap, "cell:%ld:%ld", gy, gx);
}

size_t EventBuilder::build(const Detection& d, const GnssFix& fix, char* buf, size_t cap,
                           char* msg_id_out, size_t msg_id_cap) {
    if (!fix.valid || fix.acc_m <= 0) return 0;
    char id[48], ts[25], cell[40];
    std::snprintf(id, sizeof id, "%s-%lu", device_id_, static_cast<unsigned long>(seq_));
    format_utc(fix.utc_ms, ts);
    grid_cell(fix.lat, fix.lon, cell, sizeof cell);
    float heading = std::fmod(fix.heading_deg, 360.0f);
    if (heading < 0) heading += 360.0f;
    if (heading >= 359.95f) heading = 0.0f;  // keeps the printed value below 360
    const float speed = fix.speed_kmh > 0 ? fix.speed_kmh : 0.0f;

    const int n = std::snprintf(
        buf, cap,
        "{\"event_id\":\"%s\",\"type\":\"%s\",\"timestamp\":\"%s\","
        "\"location\":{\"lat\":%.6f,\"lon\":%.6f,\"acc_m\":%.1f},\"road_segment\":\"%s\","
        "\"speed_kmh\":%.1f,\"heading\":%.1f,\"severity\":\"%s\",\"confidence\":%.2f,"
        "\"source\":\"%s\",\"model\":\"%s\"}",
        id, to_string(d.type), ts, fix.lat, fix.lon, fix.acc_m, cell, speed, heading,
        to_string(d.severity), d.confidence, device_id_, model_);
    if (n < 0 || static_cast<size_t>(n) >= cap) return 0;
    if (msg_id_out && msg_id_cap) std::snprintf(msg_id_out, msg_id_cap, "%s", id);
    ++seq_;
    return static_cast<size_t>(n);
}

}  // namespace icvsp
