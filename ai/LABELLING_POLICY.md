# Labelling policy (speed bumps and road damage)

One set of rules for everyone who labels data for the ICVSP detector, and for deciding whether a public dataset
can be used. The detector only has to find hazards the system warns about (FR-27): ones that make a driver
**stop, slow down or steer around them**.

## 1. Which images

- Only images from a **camera fixed in a vehicle** (dashcam, phone mount, or our device), looking along the road.
  This includes rear-facing and low mounts like ours.
- Not handheld photos, top-down photos, or close-ups taken standing over the hazard.
- Video is labelled as frames. Frames from one video stay together (see section 7).

## 2. The two classes

The model learns two classes. Each box also gets a **subtype** (a CVAT attribute, section 2.3), so we can measure which
kinds are missed without changing the classes. The driver sees two messages: *Speed bump ahead* (`speed_bump`, and dips
found by the IMU) and *Road damage ahead* (`pothole`).

### speed_bump

A raised hump across the lane that makes a car slow down: **marked or unmarked**, asphalt, concrete, rubber or
informal, at least about as high as a normal speed bump.

Label it: draw one box over the part of the bump that is visible across the lane.

Do **not** label as a speed bump:
- painted lines or zebra crossings with no hump
- rumble strips (rows of small ridges)
- flat road joints or level crossings
- kerbs, traffic islands, steps
- manhole covers (a raised cover is `pothole`, subtype `manhole`)
- **dips** (the road goes down across the lane): never boxed. The camera can barely see them; the IMU and fleet
  slowing find them. Tag them with `D` in `tools/review.html`.

Subtype: `built` (made as a speed bump, marked or not) or `informal` (a hump people made themselves, dirt, rope,
pipes).

### pothole (road damage)

Damage in the driving lane that makes a driver slow down or steer around it. Three kinds, all labelled `pothole`:

| Subtype | What it is |
|---|---|
| `hole` | a hole or sunken area with broken edges |
| `manhole` | a manhole or drain cover that is **sunken, raised, broken, open or missing** |
| `broken_surface` | crushed or crumbling asphalt, a failed patch: no clean hole, but a car slows down for it |

Signs that it is in scope:
- broken edges
- a dark inside or shadow showing depth
- about a wheel's width or more

Water-filled potholes count if their edges are clear.

Label it: one tight box around the hole's edges (or the cover, or the broken stretch in the lane), not the cracks
around it. Two separate holes get two boxes; holes that run together get one. A long broken stretch gets one box per
lane width of road that is visible.

Do **not** label as a pothole:
- cracks, including alligator cracking
- patched or repaired areas **level** with the road (a broken patch is `broken_surface`)
- stains, wet patches, oil, shadows
- manholes and drain grates **level** with the road (flush covers are everywhere: boxing them floods drivers with
  warnings)
- a dirt or gravel road that is unpaved everywhere (there is no damage to warn about); broken asphalt is
  `broken_surface`
- shallow surface wear that a car would just drive over

### 2.3 Subtypes and frame tags in CVAT

Set these up once in the CVAT project (*Project → Labels*):
- on `speed_bump`: attribute `subtype`, values `built`, `informal`
- on `pothole`: attribute `subtype`, values `hole`, `manhole`, `broken_surface`
- frame tags (*Tag* labels): `unsure` and `debris`

`debris` (rubble, rocks, objects on the road) is tagged on the frame but not boxed: there are too few examples to
train it yet, and debris moves, so a warning would expire within hours.

## 3. When you are not sure

- **Never leave a hazard unboxed in a frame you keep.** An unboxed hazard teaches the model that hazards are
  background.
- If you cannot tell whether something is in scope (too far, too dark, half hidden), give the frame the
  **`unsure` tag**. It is left out of training.
- The YOLO export drops frame tags, so export each task **twice**: *Ultralytics YOLO Detection 1.0* (boxes) and
  *CVAT for images 1.1* (tags and subtypes). `scripts/package_cvat.py` reads both and leaves `unsure` frames out.
- A hazard cut off at the frame edge: label it if more than half is visible, otherwise mark the frame "unsure".

## 4. Hard negatives (keep these, with no boxes)

Frames with **no hazard**, especially things that look like one, are valuable:
- manholes and drain grates **level** with the road
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
| PotHoleNet | Used; manhole class excluded | Dashcam view, consistent boxes; its manholes are mostly flush covers, so they stay hard negatives (spot-check 50 for sunken ones) |
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
- Use the existing tools: CVAT for boxes, `tools/review.html` for tagging events in video (B bump, H pothole,
  Shift = minor, D dip). Export as in section 3.
- Policy changes apply to old frames too: when a rule changes, the frames we told the model are background are
  re-checked (18 Oct: sunken manholes and broken surface, see `.plans/hazard-taxonomy-spec.md`).
