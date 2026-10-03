#pragma once
#include <cstddef>
#include <cstdint>

namespace icvsp {

// Rejects stale, future-dated and repeated messages (proposed FR-06). Times are GNSS-based Unix
// milliseconds on both sides, so devices share one clock.
//
// A message is accepted once: it must be at most `window_ms` old, at most `max_future_ms` ahead of the
// receiver's clock, and its (sender, msg_id) must not have been seen within the window. Seen IDs are kept
// in a fixed-size table (no heap use on the microcontroller); if the table is full of IDs that are still
// inside the window, new messages are refused rather than letting an old ID be forgotten and replayed.
//
// Call this only after the signature has been verified, so forged messages cannot fill the table.
enum class ReplayResult { Accept, Stale, Future, Duplicate, Busy };

const char* to_string(ReplayResult r);

class ReplayGuard {
public:
    static constexpr size_t kCapacity = 256;
    static constexpr size_t kIdLen = 64;

    explicit ReplayGuard(int64_t window_ms = 30000, int64_t max_future_ms = 2000)
        : window_ms_(window_ms), max_future_ms_(max_future_ms) {}

    ReplayResult check(const char* sender, const char* msg_id, int64_t msg_utc_ms, int64_t now_utc_ms);

    size_t size() const;

private:
    struct Entry {
        bool used;
        int64_t utc_ms;
        uint32_t hash;
        char key[kIdLen];  // sender + '\0'-separated msg_id, truncated
    };
    int64_t window_ms_, max_future_ms_;
    Entry table_[kCapacity] = {};
};

}  // namespace icvsp
