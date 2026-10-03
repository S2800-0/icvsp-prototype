#include "replay_guard.h"

#include <cstring>

namespace icvsp {

const char* to_string(ReplayResult r) {
    switch (r) {
        case ReplayResult::Accept: return "accept";
        case ReplayResult::Stale: return "stale";
        case ReplayResult::Future: return "future";
        case ReplayResult::Duplicate: return "duplicate";
        default: return "busy";
    }
}

namespace {

// Builds "sender\x1fmsg_id" (truncated to fit) and its FNV-1a hash.
uint32_t make_key(const char* sender, const char* msg_id, char* key, size_t cap) {
    size_t n = 0;
    for (const char* p = sender; *p && n + 1 < cap; ++p) key[n++] = *p;
    if (n + 1 < cap) key[n++] = '\x1f';
    for (const char* p = msg_id; *p && n + 1 < cap; ++p) key[n++] = *p;
    key[n] = '\0';
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < n; ++i) h = (h ^ static_cast<uint8_t>(key[i])) * 16777619u;
    return h;
}

}  // namespace

ReplayResult ReplayGuard::check(const char* sender, const char* msg_id, int64_t msg_utc_ms,
                                int64_t now_utc_ms) {
    if (msg_utc_ms > now_utc_ms + max_future_ms_) return ReplayResult::Future;
    if (now_utc_ms - msg_utc_ms > window_ms_) return ReplayResult::Stale;

    char key[kIdLen];
    const uint32_t h = make_key(sender, msg_id, key, sizeof key);
    Entry* free_slot = nullptr;
    for (Entry& e : table_) {
        // entries whose message is outside the window can no longer be replayed: free them
        if (e.used && now_utc_ms - e.utc_ms > window_ms_) e.used = false;
        if (!e.used) {
            if (!free_slot) free_slot = &e;
            continue;
        }
        if (e.hash == h && std::strcmp(e.key, key) == 0) return ReplayResult::Duplicate;
    }
    if (!free_slot) return ReplayResult::Busy;
    free_slot->used = true;
    free_slot->utc_ms = msg_utc_ms;
    free_slot->hash = h;
    std::memcpy(free_slot->key, key, sizeof key);
    return ReplayResult::Accept;
}

size_t ReplayGuard::size() const {
    size_t n = 0;
    for (const Entry& e : table_) n += e.used;
    return n;
}

}  // namespace icvsp
