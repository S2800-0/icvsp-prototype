# Data (not in the repository)

| Folder | Contents | How to get it |
|---|---|---|
| `raw/sbp_yolo/` | SBP-YOLO public dataset (7,375 images) | `python scripts/download_sbp.py --out data/raw/sbp_yolo`, or notebook step 3 |
| `merged/` | Unified dataset built from `raw/` | `python scripts/merge_datasets.py` |
| `egypt_test/clips/` | Team phone clips of Egyptian roads | Recorded by the team; kept private (faces and number plates) |
| `egypt_test/test/` | Labelled Egyptian test frames | Labelled by the AI team (see `../README.md`, step 3) |
