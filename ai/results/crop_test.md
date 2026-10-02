# Cropping the input to reduce computation (experiment, 2 Oct 2026)

**Question:** can the detector use fewer GFLOPs without losing accuracy? Computation grows with the
number of input pixels, and road hazards never appear in the sky or on buildings, so we tested
keeping only the bottom 60 % of each frame (crop line fixed before scoring), at 960 and 640 px width.
Model v3c, both held-out test videos, with the road check and motion check for speed bumps, conf ≥ 0.25.

Note: the commonly quoted 14.8 GFLOPs is for a square 960 × 960 input. On 16:9 video, Ultralytics
pads to 960 × 544, which is 8.3 GFLOPs: that is today's real cost.

| Input | Shape | GFLOPs | Night potholes found | Night false flags / min (pothole, bump) | Day false flags / min (pothole, bump) |
|---|---|---|---|---|---|
| Full frame, 960 | 960 × 544 | 8.3 | 4 / 6 | 1.20, 0.10 | 1.00, 0.00 |
| Full frame, 640 | 640 × 384 | 3.9 | 2 / 6 | 1.70, 0.30 | 1.39, 0.00 |
| Bottom 60 %, 960 | 960 × 352 | 5.4 | 2 / 6 | 1.50, 0.30 | 0.72, 0.00 |
| Bottom 60 %, 640 | 640 × 224 | 2.3 | 4 / 6 | 1.30, 0.20 | 0.78, 0.00 |

- Both visually confirmed potholes (4:03, 7:47) are found by all four versions; the crops keep the
  same confidence as the full frame (0.73 and 0.80 at 960).
- The differences in "potholes found" are weak, unconfirmed events (two boxes at 0.27–0.54) that
  flip between runs: within the noise of a 6-pothole test.
- Cropping lowers daytime false pothole flags by about a quarter (buildings, signs and vehicles
  higher in the frame are no longer seen).
- Full frame at 640 is the weakest option.

**Conclusion:** cropping to the road area costs no measurable accuracy and cuts computation from
8.3 to 5.4 GFLOPs at 960 px (2.3 at 640 px), i.e. from the quoted 14.8 to a third or less. The crop
line depends on the camera mount and will be set at installation (just above the horizon). Choosing
between 960 and 640 needs the hardware benchmark and a larger labelled test set.
