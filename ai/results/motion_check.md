# Motion check for speed bumps (experiment, 1 Oct 2026)

**Idea (from the team):** a speed bump is fixed to the road, so as the car approaches it the box moves
down the image and grows until the bump passes under the car. Boxes on kerbs, lane edges and road
seams stay at about the same place (each frame boxes a different piece of a continuous line), and
boxes on buses, cars and reflections move with the traffic.

**Method:** `scripts/motion_check.py` links detections of the same class in consecutive frames into
tracks and keeps a track only if it has ≥ 3 detections, moves down by ≥ 3 % of the image height at
≥ 0.02 image heights per second, and does not shrink. Thresholds fixed before scoring. Model v3c,
detections every 4th frame (15 per second). Applied to speed bumps only (see below).

**False speed-bump flags per minute on the held-out test videos (confidence ≥ 0.25):**

| | No filter | Road check | Motion check | Road + motion |
|---|---|---|---|---|
| Night (10 min) | 3.20 | 1.70 | 0.20 | 0.10 |
| Day (18 min) | 0.83 | 0.33 | 0.00 | 0.00 |

The test videos contain no confirmed real bump detection, so they cannot show whether real bumps are
lost. That was measured on the 33 bumps the team tagged in the four training videos:

| v3c on the training videos | Tagged bumps found | Bump flags / min away from tags |
|---|---|---|
| Confidence ≥ 0.25, no motion check | 21 / 33 | 2.28 |
| Confidence ≥ 0.25, motion check | 17 / 33 | 0.28 (−88 %) |
| Confidence ≥ 0.4, no motion check | 19 / 33 | 0.94 |
| Confidence ≥ 0.4, motion check | 17 / 33 | 0.18 (−81 %) |

- The motion check keeps 81–89 % of the real bumps the model finds and removes over 80 % of other bump flags.
- v3c was trained on frames from these videos, so the detection rate (21/33) is optimistic; the share
  kept by the motion rule is the meaningful number.
- Applied to potholes as well, it keeps only 11 of 16 detected potholes (potholes are often seen at an
  angle or briefly), so it is used for speed bumps only.
- On the unit, GPS speed will switch the check off while the car is (nearly) stationary, when real
  hazards do not move in the image either.

**Current pipeline:** v3c → road check (bumps) → motion check (bumps) → trust engine.
