# YouTube batch 6: spec

Goal: more Egyptian training data for the pothole and speed-bump detector, a bigger Egyptian test set, and a
retrained YOLO (v6) and chip model (ESPDet-Pico v6), with a decision rule fixed before any results are seen.

Deadline: before the next mentor meeting (tagging and labelling this week, training at the weekend).

## Videos

All from @egyptianstreetsvlog (the channel our earlier Cairo videos came from), 1080p, video only, in
`ai/data/videos/`. Private use only: never committed or published; models trained on them stay out of git.

| File | Length | Split | Tagged by |
|---|---|---|---|
| delta_yt_08_cairo_mansoura.mp4 | 1:58 | train | Rana |
| cairo_yt_09_night.mp4 | 22 min | train | Shahesta |
| cairo_yt_10_day.mp4 | 20 min | train | Shahesta |
| cairo_yt_11_day.mp4 | 19 min | train | Rana |
| cairo_yt_12_sunset.mp4 | 29 min | train | Shahesta |
| cairo_yt_13_corniche_night.mp4 | 29 min | train | Rana |
| cairo_yt_14_night_test.mp4 | 27 min | **test** | Shahesta |
| cairo_yt_15_day_test.mp4 | 25 min | **test** | Shahesta |

Split by video, never by frame. Test videos are never harvested, pre-labelled or trained on.
Known limit: the test videos share the channel (same car and camera) with the training videos, so results may be
better than on another car; accepted for now, the real test set is our own recordings.

## Roles

- **Shahesta:** tags 3 training videos and both test videos; corrects boxes; checks all of Rana's boxes.
- **Rana:** tags 3 training videos (including the 2-hour Delta drive); corrects boxes on her videos.
- Both follow `ai/LABELLING_POLICY.md`.
- Time budget: about 6–8 hours in total, so about 800 frames to correct.

## Steps

### 1. Register the videos
Add the 8 rows to `ai/data/videos/videos.csv` with `split=train` or `split=test`, source URL and time of day.

### 2. Severity keys in the tagging tool (tool change)
`tools/review.html`:
- **B / H** = severe bump / pothole
- **Shift+B / Shift+H** = minor bump / pothole
- The exported `events.csv` gains a `severity` column (`severe` / `minor`).
- `event_recall.py` and `firmware/tools/score_logic.py` gain an option to score only severe hazards.

### 3. Tag the hazards
- **Training videos:** watched at 2x, split as in the table. Every hazard tagged, severe or minor.
- **Test videos:** watched at normal speed, every hazard tagged with severity. This becomes the new Egyptian test
  set, next to the existing Cairo night and day videos.

### 4. Pick the frames (`harvest_frames.py`, v5 model)
- **Tag mode:** 3 frames before each training tag (2.5, 1.5 and 0.5 s before).
- **Hard negatives:** frames where v5 draws a box but no tag is nearby (`--avoid-events`), capped at about 80 per
  video. The labeller deletes the wrong box, which teaches the model what not to flag.
- A few random road frames per video.
- Target: about 800 frames in total.

### 5. Pre-label and correct
- `prelabel.py` with v5 draws boxes on every harvested frame.
- Correct in a **private CVAT cloud project** (app.cvat.ai), shared only with Shahesta and Rana; export YOLO format.
- **Minor potholes:** mark the whole frame "unsure", so it is left out of training. Never box them, never leave
  them unboxed.
- **Review:** Shahesta checks all of Rana's frames. Disagreements are added to the labelling policy as examples.

### 6. Package
`apply_review.py --name youtube_batch6` → zips in `data/reviewed/`; upload as a new version of the private Kaggle
dataset `icvsp-egypt-train`.

### 7. Retrain (Kaggle)
- **YOLO v6:** the v5 recipe and data plus `youtube_batch6` (starting from v5 weights). Judged by the final epoch.
- **ESPDet-Pico v6:** the same data, trained right after YOLO.
- Each experiment gets its own name in the notebook; both new model files are added to `.gitignore`.

### 8. Score
- On the new test videos (14, 15) and the existing Cairo night and day videos.
- Device logic applied (current pothole rule), counting **severe hazards only**.
- Measured: hazards found and false reports per minute, for v5 against v6, and ESPDet against ESPDet v6.
- Also the public SBP test (last epoch), and the simulation with each new model's measured rates
  (`sil/compare_seeds.py`).

## Decision rule (fixed now, before results)

v6 replaces v5, and ESPDet v6 replaces ESPDet, only if, on the Egyptian test videos with the device logic:
1. it finds **at least as many severe hazards**, and
2. it has **no more false reports per minute**, and
3. the public SBP test mAP50 drops by **no more than 0.01**.

Otherwise the current model stays, and the results are recorded in `ai/results/experiments.md` either way.

## Out of scope

- Our own recordings: they join a later batch and become the main test set.
- Test videos from other channels.
- Changing the device-logic thresholds: only after the new test set exists, and checked on a video not used to
  choose them.

## Open

- Date of the next mentor meeting, which sets the exact deadline.
