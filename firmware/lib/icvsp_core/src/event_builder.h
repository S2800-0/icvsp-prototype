#pragma once
#include <cstddef>
#include <cstdint>

#include "types.h"

namespace icvsp {

// Turns a detection plus the current GNSS fix into a safety event in the format of
// engine/schema/safety_event.schema.json.
//
// The JSON is written in one fixed field order with fixed number formats, so the exact bytes are
// reproducible: these bytes are what gets signed (no separate canonicalisation step is needed).
class EventBuilder {
public:
    EventBuilder(const char* device_id, const char* model) : device_id_(device_id), model_(model) {}

    // Writes the event into buf. Returns the length, or 0 if there is no valid fix or buf is too small.
    // msg_id_out (optional) receives the event_id.
    size_t build(const Detection& d, const GnssFix& fix, char* buf, size_t cap,
                 char* msg_id_out = nullptr, size_t msg_id_cap = 0);

    uint32_t next_seq() const { return seq_; }

private:
    const char* device_id_;
    const char* model_;
    uint32_t seq_ = 1;
};

// ISO 8601 UTC with milliseconds, e.g. 2026-10-03T01:02:03.456Z (24 characters + NUL).
void format_utc(int64_t utc_ms, char out[25]);

// Coarse grid cell (about 200 m) used as road_segment until the backend maps reports to real roads.
void grid_cell(double lat, double lon, char* out, size_t cap);

}  // namespace icvsp
