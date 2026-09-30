# ICVSP · AI: pothole & speed-bump detector

Goal of this first experiment: find out whether **public datasets alone** are enough to detect
Egyptian speed bumps, before the team spends time collecting and labelling Egyptian data.

## Folder layout

```
ai/
├── ICVSP_train.ipynb        # run this in Google Colab (free GPU, no Drive access): download → merge → train → evaluate
├── configs/classes.yaml     # unified classes (pothole, speed_bump) + how source classes map onto them
├── scripts/
│   ├── download_sbp.py      # fetch the public SBP-YOLO dataset, no Google sign-in
│   ├── build_notebook.py    # regenerate the Colab notebook from these files
│   ├── merge_datasets.py    # remap classes, drop unwanted ones, remove duplicates, split
│   ├── train.py             # YOLO11n training (Colab GPU or Mac MPS)
│   ├── evaluate.py          # public + Egyptian test, applies the decision rule
│   ├── predict_clips.py     # draws the model's boxes on Egyptian clips (visual check)
│   ├── compare_runs.py      # builds results/experiments.md comparing all runs
│   └── extract_frames.py    # phone clips → frames for labelling
├── data/
│   ├── raw/                 # one sub-folder per source dataset (YOLO format)
│   ├── merged/              # created by merge_datasets.py
│   └── egypt_test/          # the team's clips, frames and labelled test set
├── runs/                    # raw training and evaluation outputs (from icvsp_results.zip)
└── results/                 # experiment log + confusion matrices per run (for the thesis)
```

## Step 1: train on public data (Colab, no Google Drive access)

1. Go to <https://colab.research.google.com> → **Upload** → choose `ai/ICVSP_train.ipynb`.
2. **Runtime → Change runtime type → T4 GPU**.
3. Run the cells in order. The notebook writes the scripts into Colab, downloads the public
   SBP-YOLO dataset (7,375 images of speed bumps and potholes, from the authors of
   [SBP-YOLO](https://github.com/chuanqi1997/SBP-YOLO)) without signing in, then merges, trains and evaluates.
4. **Stop at step 4 of the notebook** the first time and check the class IDs: the dataset has no class
   names, and `configs/classes.yaml` assumes 0 = pothole, 1 = speed bump.
5. Run the last cell to download `icvsp_results.zip` before closing. Colab deletes everything at the end of the session.

**Long runs (v1, v2): use Kaggle instead.** Free Colab can cut off a multi-hour run when its GPU quota runs out.
`ICVSP_train_kaggle.ipynb` runs unattended on Kaggle (about 30 GPU hours per week, 12 hours per run) and keeps its outputs:
1. [kaggle.com](https://www.kaggle.com) → **Create → New Notebook → File → Import Notebook** → choose `ai/ICVSP_train_kaggle.ipynb`.
2. Right panel → *Session options*: **Accelerator GPU T4 x2**, **Internet On** (needs a phone-verified account).
3. Set `EXPERIMENT` in the Settings cell, then **Save Version → Save & Run All (Commit)**. You can close the browser.
4. When it finishes, download `icvsp_results.zip` from the version's **Output** tab.

If a run is cut off, attach that version's output (*Add Input → Your Work*), set `RESUME_FROM` to its `last.pt`, and
commit again: training continues from the last saved epoch (`scripts/train.py --resume`).

After editing any script or config here, run `python scripts/build_notebook.py` to regenerate both notebooks.

Optional extra sources: download them in YOLO format into their own folder under `data/raw/`
(e.g. `data/raw/indian_roads/`). The merge picks up every folder automatically.
- Indian Roads Dataset (Kaggle): marked + unmarked speed breakers, potholes
- Roboflow Universe "Speed Bump Detection" (1,692 images) and "Road Defects"
- RDD2022 (13 GB, potholes = class D40): too large for a laptop; download inside Colab only

## Recording experiments

After each Colab session, unzip `icvsp_results.zip` into `ICVSP/ai/` and run
`python scripts/compare_runs.py`. It updates `results/experiments.md` (every run versus the
`v0_public_merge` baseline) and copies each run's confusion matrices and curves into `results/<run>/`.

## Step 2: record Egyptian test clips (everyone)

- Passenger holds or mounts the phone (never the driver), centred on the windscreen, landscape.
- **1080p, 30 fps, High Efficiency** (not 4K). Start about 10 s before a bump you know; stop just after it.
- 3–4 clips each on your **normal daily route**. Different areas matter more than more clips.
- Name clips `<area>_<name>_<n>.mov` (e.g. `maadi_malak_1.mov`) and upload them to
  `ai/data/egypt_test/clips/` on the shared Drive the same day, then delete them from the phone.

**Quick visual check:** notebook step 9 runs the model on uploaded clips and returns the videos with
boxes drawn on them. This shows what it detects but not what it misses, so it is not the final test.

## Step 3: pre-label, correct, measure (Colab notebook steps 9–10, no GPU needed)

1. **Step 9:** upload the clips. It returns `egypt_clips_check.zip` (videos with the model's boxes, for a first look) and
   `prelabels.zip` (2 frames per second, already labelled by the model).
2. **Correct the labels in [CVAT](https://app.cvat.ai)** (private, unlike Roboflow's free plan; the clips show faces and plates):
   create a task with labels exactly `pothole` and `speed_bump`, upload the images, *Upload annotations → Ultralytics YOLO
   Detection 1.0* with `prelabels.zip`, then check **every** frame: fix boxes, delete false ones, **add missed ones**.
   Export as *Ultralytics YOLO Detection 1.0, with images*.
3. **Step 10:** upload that export. `prepare_egypt_test.py` builds the test set (and warns if it has fewer than
   about 120 speed bumps or fewer than 5 areas), then `evaluate.py` prints the decision.

## Step 4: decide

`python scripts/evaluate.py --weights runs/v0_public_merge/weights/best.pt`

| Egyptian speed-bump result | Decision |
|---|---|
| recall ≥ 0.75 and precision ≥ 0.70 | **Enough.** Optionally fine-tune on a few Egyptian images. |
| recall 0.50 – 0.75 | **Top-up.** Look at the misses and collect 50–100 images of those cases only. |
| recall < 0.50 | **Not enough.** Build a proper Egyptian training set. |

The thresholds are fixed in `scripts/evaluate.py` before testing, so the decision stays honest.

## Licences

- SBP-YOLO repository: GPL-3.0. Cite Liang et al., *J. Real-Time Image Processing* 23, 52 (2026).
- RDD2022: CC BY 4.0. Mapillary images: CC BY-SA 4.0.
- Ultralytics YOLO: AGPL-3.0 (fine for the graduation project).
- Blur faces and number plates before publishing any Egyptian images (Egypt PDPL, Law 151/2020).
