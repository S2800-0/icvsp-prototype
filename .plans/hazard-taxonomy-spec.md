# Hazard types and who detects them: spec

Goal: make the detector and the whole system accurate enough for a product-level prototype on Egyptian roads, by
fixing *what* we call a hazard and *which sensor* is responsible for each kind, before collecting or training more.

Why now (18 Oct): more data and training stopped moving the numbers (public test: v3c 0.846, v5 0.841, v6 0.843;
Egyptian test: v6 10 / 29 against v5 7 / 29, inside the noise of a 29-hazard test set). Checking each tagged bump showed
the model never saw 8 of 14 bumps and the device rules rejected 4 more. The labelling policy also told labellers to treat
sunken manholes and broken road as background, although drivers slow down or swerve for them (FR-27 scope).

## 1. Hazard groups

Grouped by what the driver must do, then by which sensor can see it.

| Group | Examples | Camera class | Camera | IMU | Fleet slowing | Driver sees |
|---|---|---|---|---|---|---|
| Raised | built speed bump (marked or not), informal hump | `speed_bump` | yes (visible ones) | yes | yes | **Speed bump ahead** |
| Sunken across the lane | dip, drainage channel | none | no | yes | yes | **Speed bump ahead** |
| Local hole | pothole; manhole that is sunken, raised, broken or open | `pothole` | yes | yes | yes | **Road damage ahead** |
| Broken surface | crushed or crumbling road, failed patch | `pothole` | yes | yes (rough stretch) | yes | **Road damage ahead** |
| Object on the road | debris, rubble, rocks | none yet (tagged only) | later | if hit | maybe | not warned yet |

Rules:
- A **flush manhole or grate** stays background (otherwise every cover becomes a false alarm). Sunken, raised, broken,
  open or missing covers are hazards.
- Dips are never boxed for the camera; the IMU measures the drop and fleet slowing confirms it.
- The driver sees two words only: *Speed bump* or *Road damage*. The model does not tell the subtypes apart yet, so
  more specific labels would often be wrong.
- Severity scope (FR-27) is unchanged: only hazards that make a car stop, slow down or change path.

## 2. Labelling changes (`ai/LABELLING_POLICY.md`)

1. Section 2 *pothole*: add sunken / raised / broken / open manholes and crushed or broken surface as `pothole`.
   Remove "manholes and drain grates", "patched or repaired areas" (only when broken, not when level) and "gravel or
   unpaved road surface" (only when broken asphalt, not a dirt road) from the *do not label* list; keep flush covers,
   level patches and dirt roads as background.
2. Section 4 *hard negatives*: flush manholes and grates stay here, explicitly "flush only".
3. New **subtype attribute** on every box in CVAT (not a class; training still uses 2 classes):
   - `speed_bump`: `built` / `informal`
   - `pothole`: `hole` / `manhole` / `broken_surface`
   - frame tag `debris` (no box is trained yet)
4. Dips: tag `D` in `tools/review.html` (already exists, not scored for the camera). Re-watch the 9 bumps the model
   never or barely saw on the test videos and re-tag any that are dips.
5. **Unsure frames must survive export.** The YOLO export drops CVAT's "unsure" mark (seen in batch 6). Use a CVAT frame
   tag `unsure` and export the tags list with the task (CVAT for images 1.1 XML) next to the YOLO export;
   `apply_review.py` leaves those frames out.

Targeted re-check of old data: only frames we told the model are "nothing" (`background_batch1` 113,
`background_batch5` 47, `background_batch6` 269, hard negatives in `youtube_batch5` 113: about 540 frames). Any frame
showing a sunken / raised / open manhole or broken surface gets a box (or `unsure`). Split with Rana; Shahesta checks all
of Rana's. Public datasets are not re-labelled.

## 3. Device rule for speed bumps (`firmware/lib/icvsp_core/src/hazard_tracker`)

Today a bump box must slide down the image (motion check) to be accepted; a box that stays still is treated as a kerb.
At a bump the car slows down, so the box stops moving and real bumps are rejected (Cairo day test, 8:33: six frames, box
fixed at 0.79 of the height).

Change: a still box is accepted when the car itself is **slowing** (GPS speed drops by a set share within the window) or
the **IMU feels a jolt** in the same window; at steady speed the motion check stays as it is (kerbs, parked cars).
Unit tests for both cases. Thresholds fixed before scoring, checked on a video not used to choose them.

Note: the YouTube test videos have no GPS speed, so on them only the IMU-free part can be checked (the rule must not add
false bumps at steady speed); the slowing part is checked in the simulation and on our own recordings.

## 4. Fleet slowing as evidence (trust engine)

Phones already send speed with each position (`POST /beacons`). When cars that pass a spot **slow sharply within about
50 to 100 m and speed back up right after** (compared with each car's own speed just before and after), that spot gets
slowing evidence.

- **Supporting evidence only:** it strengthens a hazard a device already reported, and never creates a warning or
  advisory alone. False warnings must stay at 0.
- **Not a type:** it says "something here"; the type comes from the IMU or camera report.
- **Jams are not hazards:** a long slow stretch (no recovery within the distance) is ignored.
- Each car counts once per pass; slowing from the reporting car itself is not counted twice.
- Engine tests: shops, crossings and police checks in the simulation (cars slow with no hazard) must not create
  warnings; a hazard with a weak report plus fleet slowing reaches a warning sooner than without.
- Measured in `sil/` first (warned in time, false warnings, all scenarios and 5 seeds), then on our own recordings.

## 5. Expiry by type (`engine/icvsp/config.py`, `max_age_s` / `freshness_tau_s`)

Already per type; values change and the new group is added:

| Type | Today | Proposed |
|---|---|---|
| Built speed bump | 14 days | about 90 days |
| Road damage (pothole, manhole, broken surface) | 48 hours | about 14 days |
| Debris (when added) | none | a few hours |

A new report or fleet slowing renews a hazard; a car passing with no jolt and no slowing counts against it (existing
negative evidence). New values are checked in the simulation before they are adopted.

## 6. Test set

- At least **50 speed bumps and 50 road-damage hazards** (manholes included), day and night, split by video, before
  models are compared again. Today: 14 bumps and 15 potholes.
- Scored per subtype, so we can see which kinds are missed.
- Our own recordings with the real device become the main test set as soon as the hardware is ready; YouTube test
  videos stay as a second set.
- The fixed decision rule (batch 6 spec) still applies, now on this larger set.

## 7. Order of work

1. Labelling policy update, CVAT subtype attribute and `unsure` tag, review.html help text (Shahesta).
2. Re-tag dips among the 9 missed test bumps (Shahesta, about 5 minutes).
3. Targeted re-check of about 540 old background frames (Shahesta + Rana).
4. Bump rule change with tests, checked on a held-out video.
5. Fleet slowing in the trust engine, measured in simulation.
6. Bigger test set: tag more test videos to 50 + 50; own recordings when the hardware is ready.
7. Only then: retrain (v7 = v5 recipe + corrected old frames + new data) and compare with the fixed rule.

Before the next mentor meeting: steps 1 to 4.

## Out of scope for now

- A debris camera class (tagged only, until a few hundred examples).
- New camera classes for manholes, broken surface or dips.
- Telling subtypes apart in the app.

## Sources

- Smartphone bump detection with crowd data: https://scholar.ptuk.edu.ps/handle/123456789/443
- Speed profiles from probe data, leaving out congested periods: https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9958283
- Crowdsourced pavement anomaly detection: https://ir.lib.nycu.edu.tw/handle/11536/134607?mode=full
- GPS phones as traffic probes (Mobile Millennium): https://its.berkeley.edu/node/5602
- Egyptian crowdsourced potholes, bumps, manholes, rumble strips: https://cscgp.miuegypt.edu.eg/?p=935
