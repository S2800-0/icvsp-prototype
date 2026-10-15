# Labelling policy (potholes and speed bumps)

One set of rules for everyone who labels data for the ICVSP detector, and for deciding whether a public dataset
can be used. The detector only has to find hazards the system warns about (FR-27): ones that make a driver
**stop, slow down or steer around them**.

## 1. Which images

- Only images from a **camera fixed in a vehicle** (dashcam, phone mount, or our device), looking along the road.
  This includes rear-facing and low mounts like ours.
- Not handheld photos, top-down photos, or close-ups taken standing over the hazard.
- Video is labelled as frames. Frames from one video stay together (see section 7).

## 2. The two classes

### speed_bump

A raised hump across the lane that makes a car slow down: **marked or unmarked**, asphalt, concrete, rubber or
informal, at least about as high as a normal speed bump.

Label it: draw one box over the part of the bump that is visible across the lane.

Do **not** label as a speed bump:
- painted lines or zebra crossings with no hump
- rumble strips (rows of small ridges)
- flat road joints or level crossings
- kerbs, traffic islands, steps
- manhole covers

### pothole

A hole or sunken area in the driving lane, deep or wide enough that a driver would slow down or steer around it.
Signs of this:
- broken edges
- a dark inside or shadow showing depth
- about a wheel's width or more

Water-filled potholes count if their edges are clear.

Label it: one tight box around the hole's edges, not the cracks around it. Two separate holes get two boxes; holes
that run together get one.

Do **not** label as a pothole:
- cracks, including alligator cracking
- patched or repaired areas level with the road
- stains, wet patches, oil, shadows
- manholes and drain grates
- gravel or unpaved road surface
- shallow surface wear that a car would just drive over

## 3. When you are not sure

- **Never leave a hazard unboxed in a frame you keep.** An unboxed hazard teaches the model that hazards are
  background.
- If you cannot tell whether something is in scope (too far, too dark, half hidden), mark the **whole frame as
  "unsure"**. It is left out of training.
- A hazard cut off at the frame edge: label it if more than half is visible, otherwise mark the frame "unsure".

## 4. Hard negatives (keep these, with no boxes)

Frames with **no hazard**, especially things that look like one, are valuable:
- manholes and drain grates
- shadows across the road
- patches, puddles and painted lines
- kerbs and traffic islands
- buses and road edges at night

Keep them with an empty label file. Name their folders `background_*`, as the merge script expects.

## 5. Size and severity, for test recordings

For every hazard in a **test** recording, note in the events file (`clip,time,type,severity,note`):
- `severity`: `severe` (in scope) or `minor` (out of scope)
- `note`: the estimated size

When you can do it safely, **measure** some bumps (height, cm) and potholes (depth and width, cm) with a tape. These
measurements set what "a normal speed bump" means in centimetres, and calibrate the IMU severity thresholds.

## 6. Public datasets

Before adding a public dataset:
1. Look at 50 random boxes from it. They must meet sections 1 and 2.
2. Check that each of its classes maps cleanly onto `pothole` or `speed_bump`, or is dropped.
3. Record the decision and the reason in `configs/classes.yaml`.

If only part of a dataset fits, keep that part with a merge profile. For example, v5 keeps Indian Roads images
with speed bumps only. Decisions so far:

| Dataset | Decision | Why |
|---|---|---|
| SBP-YOLO | Used, keeps the public test split | Baseline |
| PotHoleNet | Used; manhole class excluded | Dashcam view, consistent boxes; manholes become hard negatives |
| Indian Roads | Speed bumps only (v5) | Its pothole images are ground-level photos of rough patches |
| BharatPothole | Not used (v5) | Many tiny, shallow marks labelled as potholes: below scope, inconsistent |

## 7. Splits

- Split **by video or trip**, never by frame. Neighbouring frames are near copies, so splitting by frame inflates
  results.
- **Test recordings are never used for training,** in any form: frames, crops or pre-labels.
- The SBP-YOLO test split stays fixed, so every model is compared on the same images.

## 8. Checking the labels

- A second person checks a random 10% of each batch.
- Every disagreement is settled and, if needed, added to this policy as an example.
- Use the existing tools: CVAT for boxes, `tools/review.html` for tagging events in video. Export in YOLO format.
