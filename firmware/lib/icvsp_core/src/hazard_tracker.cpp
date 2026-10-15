#include "hazard_tracker.h"

#include <cmath>

namespace icvsp {

namespace {
int popcount(uint32_t v) {
    int n = 0;
    for (; v; v &= v - 1) ++n;
    return n;
}
}  // namespace

size_t HazardTracker::active_tracks() const {
    size_t n = 0;
    for (const Track& tr : tracks_) n += tr.used;
    return n;
}

bool HazardTracker::on_road(const CamDetection& d) const {
    if (d.road_frac >= 0) return d.road_frac >= cfg_.min_road_frac;
    const float cy = (d.y1 + d.y2) / 2;
    return cy >= cfg_.road_top;  // the road is the lower part of the image, facing forward or backward
}

bool HazardTracker::moves_like_fixed_object(const Track& tr) const {
    if (tr.n_points < cfg_.k_hits || tr.n_points < 2) return false;
    const Point& a = tr.points[0];
    const Point& b = tr.points[tr.n_points - 1];
    if (b.t_ms <= a.t_ms) return false;
    // forward camera: the hazard moves down (+y) and grows; rear camera: up (-y) and shrinks
    const float dir = cfg_.rear_camera ? -1.0f : 1.0f;
    const float drop = dir * (b.cy - a.cy);
    // least-squares slope of the centre height over time, in image heights per second
    double st = 0, sy = 0, stt = 0, sty = 0;
    for (uint8_t i = 0; i < tr.n_points; ++i) {
        const double t = (tr.points[i].t_ms - a.t_ms) / 1000.0, y = tr.points[i].cy;
        st += t; sy += y; stt += t * t; sty += t * y;
    }
    const double n = tr.n_points, den = n * stt - st * st;
    const double rate = den > 0 ? dir * (n * sty - st * sy) / den : 0.0;
    const bool size_ok = cfg_.rear_camera ? b.w <= a.w : b.w >= a.w;
    return drop >= cfg_.min_drop && rate >= cfg_.min_rate && size_ok;
}

bool HazardTracker::confirmed(const Track& tr) const {
    const uint32_t window = cfg_.n_frames >= 32 ? 0xFFFFFFFFu : ((1u << cfg_.n_frames) - 1);
    const int hits = popcount(tr.hit_frames & window);
    // potholes: k_hits frames, or fewer when the track's best box is confident (they pass a forward camera fast)
    const bool short_pothole = tr.type == HazardType::Pothole && cfg_.pothole_k_hits > 0 &&
                               hits >= cfg_.pothole_k_hits && tr.max_conf >= cfg_.pothole_min_peak_conf;
    if (hits < cfg_.k_hits && !short_pothole) return false;
    if (tr.type == HazardType::SpeedBump && !moves_like_fixed_object(tr)) return false;
    return true;
}

size_t HazardTracker::update(uint32_t t_ms, float speed_kmh, const CamDetection* dets, size_t n,
                             CameraHazard* out, size_t cap) {
    // a new frame: age every track by one frame, close tracks that were not seen for too long
    for (Track& tr : tracks_) {
        if (!tr.used) continue;
        tr.hit_frames <<= 1;
        if (t_ms - tr.t_last_ms > cfg_.max_gap_ms) tr.used = false;
    }

    // link detections to tracks, strongest first, each track at most once per frame
    bool taken[kMaxTracks] = {};
    bool done[64] = {};
    const size_t m = n < 64 ? n : 64;
    for (size_t k = 0; k < m; ++k) {
        int best = -1;
        for (size_t i = 0; i < m; ++i)
            if (!done[i] && (best < 0 || dets[i].conf > dets[best].conf)) best = static_cast<int>(i);
        if (best < 0) break;  // cannot happen (k < m), kept so the index is provably in range
        done[best] = true;
        const CamDetection& d = dets[best];
        if (d.conf < cfg_.min_conf) continue;
        if (d.type == HazardType::SpeedBump && !on_road(d)) continue;
        const float cx = (d.x1 + d.x2) / 2, cy = (d.y1 + d.y2) / 2, w = d.x2 - d.x1;

        int match = -1;
        float best_dist = cfg_.max_jump;
        for (size_t i = 0; i < kMaxTracks; ++i) {
            const Track& tr = tracks_[i];
            if (!tr.used || taken[i] || tr.type != d.type) continue;
            const float dist = std::hypot(cx - tr.cx, cy - tr.cy);
            if (dist <= best_dist) { best_dist = dist; match = static_cast<int>(i); }
        }
        if (match < 0) {  // start a new track in a free slot (or replace the oldest one)
            int slot = -1;
            for (size_t i = 0; i < kMaxTracks; ++i)
                if (!tracks_[i].used) { slot = static_cast<int>(i); break; }
            if (slot < 0) {
                slot = 0;
                for (size_t i = 1; i < kMaxTracks; ++i)
                    if (tracks_[i].t_last_ms < tracks_[slot].t_last_ms) slot = static_cast<int>(i);
            }
            tracks_[slot] = Track{};
            tracks_[slot].used = true;
            tracks_[slot].type = d.type;
            tracks_[slot].t_first_ms = t_ms;
            match = slot;
        }
        Track& tr = tracks_[match];
        taken[match] = true;
        tr.cx = cx; tr.cy = cy; tr.t_last_ms = t_ms;
        tr.hit_frames |= 1u;
        tr.hits++;
        if (d.conf > tr.max_conf) tr.max_conf = d.conf;
        if (tr.n_points < kMaxPoints) tr.points[tr.n_points++] = Point{t_ms, cy, w};
    }

    // report tracks that have just become confirmed (only while the car moves)
    size_t emitted = 0;
    if (speed_kmh < cfg_.min_speed_kmh) return 0;
    for (Track& tr : tracks_) {
        if (!tr.used || tr.reported || emitted >= cap || !confirmed(tr)) continue;
        tr.reported = true;
        out[emitted++] = CameraHazard{tr.type, tr.t_first_ms, t_ms, tr.max_conf, tr.hits, tr.cx, tr.cy};
    }
    return emitted;
}

}  // namespace icvsp
