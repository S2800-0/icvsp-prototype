// Runs the firmware's camera decision logic (HazardTracker) over per-frame detections saved from a video,
// so the accuracy of the complete logic can be measured on real footage on a laptop.
//
// Input: a detections CSV from ai/scripts/predict_clips.py (clip,t_s,class,conf,x1,y1,x2,y2[,...,road_frac_ctx])
// Output (stdout): one row per confirmed hazard, in the same CSV format, at the time it was confirmed,
// so ai/scripts/event_recall.py can score it.
//
//   replay_detections detections.csv [--fps 15] [--forward] [--road-mask] [--speed 40]
//
// --forward   the footage comes from a forward-facing camera (the YouTube test videos); the device default
//             is a rear camera.
// --road-mask use the road_frac_ctx column (SegFormer road mask) instead of the fixed road region.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "hazard_tracker.h"

using namespace icvsp;

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "usage: replay_detections detections.csv [--fps 15] [--forward] [--road-mask] [--speed 40] [--pothole-k 2 --pothole-conf 0.5] [--k 3]\n"); return 2; }
    double fps = 15.0, speed = 40.0;
    bool forward = false, road_mask = false;
    int pothole_k = -1, k_all = 0;          // -1 / < 0: keep the device defaults
    double pothole_conf = -1.0;
    for (int i = 2; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--fps") && i + 1 < argc) fps = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--speed") && i + 1 < argc) speed = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--forward")) forward = true;
        else if (!std::strcmp(argv[i], "--road-mask")) road_mask = true;
        else if (!std::strcmp(argv[i], "--pothole-k") && i + 1 < argc) pothole_k = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--pothole-conf") && i + 1 < argc) pothole_conf = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--k") && i + 1 < argc) k_all = std::atoi(argv[++i]);
    }
    std::ifstream in(argv[1]);
    std::string line;
    auto chomp = [](std::string& l) { if (!l.empty() && l.back() == '\r') l.pop_back(); };  // CSVs written with \r\n
    std::getline(in, line);
    chomp(line);
    std::vector<std::string> head;
    { std::stringstream ss(line); std::string c; while (std::getline(ss, c, ',')) head.push_back(c); }
    auto col = [&](const char* name) { for (size_t i = 0; i < head.size(); ++i) if (head[i] == name) return (int)i; return -1; };
    const int c_clip = col("clip"), c_t = col("t_s"), c_cls = col("class"), c_conf = col("conf");
    const int c_x1 = col("x1"), c_y1 = col("y1"), c_x2 = col("x2"), c_y2 = col("y2"), c_road = col("road_frac_ctx");
    if (c_clip < 0 || c_t < 0 || c_cls < 0 || c_conf < 0 || c_y2 < 0) { std::fprintf(stderr, "missing columns\n"); return 2; }
    if (road_mask && c_road < 0) { std::fprintf(stderr, "no road_frac_ctx column\n"); return 2; }

    std::map<std::string, std::map<long, std::vector<CamDetection>>> clips;  // clip -> frame -> detections
    while (std::getline(in, line)) {
        chomp(line);
        std::vector<std::string> f; std::stringstream ss(line); std::string c;
        while (std::getline(ss, c, ',')) f.push_back(c);
        if ((int)f.size() <= c_y2) continue;
        CamDetection d;
        d.type = f[c_cls] == "pothole" ? HazardType::Pothole : HazardType::SpeedBump;
        d.conf = std::stof(f[c_conf]);
        d.x1 = std::stof(f[c_x1]); d.y1 = std::stof(f[c_y1]); d.x2 = std::stof(f[c_x2]); d.y2 = std::stof(f[c_y2]);
        d.road_frac = road_mask ? std::stof(f[c_road]) : -1.0f;
        clips[f[c_clip]][std::lround(std::stod(f[c_t]) * fps)].push_back(d);
    }

    TrackerConfig cfg;
    cfg.rear_camera = !forward;
    if (pothole_k >= 0) cfg.pothole_k_hits = (uint8_t)pothole_k;   // --pothole-k 0 switches the short rule off
    if (pothole_conf >= 0) cfg.pothole_min_peak_conf = (float)pothole_conf;
    if (k_all > 0) cfg.k_hits = (uint8_t)k_all;
    std::printf("clip,t_s,class,conf,x1,y1,x2,y2\n");
    for (auto& [clip, frames] : clips) {
        HazardTracker tr(cfg);
        const long last = frames.rbegin()->first;
        CameraHazard out[8];
        for (long k = 0; k <= last + 1; ++k) {  // every processed frame, also those without detections
            auto it = frames.find(k);
            const CamDetection* d = it == frames.end() ? nullptr : it->second.data();
            const size_t n = it == frames.end() ? 0 : it->second.size();
            const size_t m = tr.update((uint32_t)std::lround(k * 1000.0 / fps), (float)speed, d, n, out, 8);
            for (size_t i = 0; i < m; ++i)
                std::printf("%s,%.3f,%s,%.3f,%.4f,%.4f,%.4f,%.4f\n", clip.c_str(), out[i].t_ms / 1000.0,
                            to_string(out[i].type), out[i].conf, out[i].cx - 0.01, out[i].cy - 0.01,
                            out[i].cx + 0.01, out[i].cy + 0.01);
        }
    }
    return 0;
}
