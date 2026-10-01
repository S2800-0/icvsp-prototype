# Road-surface filter for detections (experiment, 1 Oct 2026)

**Question:** many false speed-bump boxes sit on kerbs, pavements, buses and parked cars. Does
dropping boxes that are not on the road remove them without losing real hazards?

**Method:** `scripts/road_filter.py` runs NVIDIA SegFormer-B0 (Cityscapes: road, sidewalk, car, bus, ...)
on every frame where model v3b fired, and records the share of road pixels inside each box and in
the box enlarged by 50 % (a pothole is itself a dark hole, and wet asphalt at night is patchy).
Rules fixed before scoring: **strict** = road ≥ 50 % inside the box; **lenient** = road ≥ 30 % around it.
Test videos: the two held-out Cairo videos (night 10 min, day 18 min). Scoring as in `event_recall.py`.

| v3b, confidence ≥ 0.25 | No filter | Lenient | Strict |
|---|---|---|---|
| Day: false bump flags / min | 0.72 | 0.17 (−76 %) | 0.11 (−85 %) |
| Night: false bump flags / min | 2.20 | 1.20 (−45 %) | 1.00 (−55 %) |
| Day: false pothole flags / min | 1.56 | 1.39 | 1.22 |
| Night: false pothole flags / min | 1.40 | 1.30 | 1.30 |

- Every pothole detection confirmed by eye survives (night 4:03 and 7:47, day 10:46; 90–100 % road).
- The day bump "hit" at 8:33 is removed, but it was a box on a kerb (checked by eye): a correct removal.
- The day pothole at 10:50 drops below the 2-box rule because its second box, on a car's brake light, is removed.
- Pothole false alarms are mostly **on** the road (patches, cracks, stains), so the filter barely changes them.
- Remaining false bump boxes: median kerbs inside the road, distant road texture and reflections at
  night, and the black letterbox bar of the day video (an artefact of the YouTube footage only).

**Conclusion:** use the road filter for speed bumps (lenient rule, which is safer at night). On the unit
it only needs to run on frames where a bump was detected. Limits: Cityscapes is daytime European
roads, so the mask is patchy at night; 8 bumps and 8 potholes in the test videos are too few to prove it.
