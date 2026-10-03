# ESPDet-Pico on the ESP32-S3 (ESP-IDF + ESP-DL, run in QEMU)

The Lite-tier camera model (ESPDet-Pico, 8-bit `.espdl`) running in Espressif's ESP-DL library on the ESP32-S3.
The emulator has no camera, so eight public test images are stored in flash, already letterboxed to the
model's 224 × 224 input. Every detection is printed as

    RESULT <sample> <class> <score> <x1> <y1> <x2> <y2>

to be compared with `main/samples/reference.json`: the float and the ESP-PPQ-simulated 8-bit detections of
the same images, written by the same script that builds the model.

## Run it

```bash
# 1. the 8-bit model + the test images and reference (ai/ folder, ESP-PPQ venv)
third_party/venv-ppq/bin/python scripts/espdet_quantize.py --weights models/espdet_pico_224.pt --imgsz 224 --samples 8 --skip-val

# 2. build and run in Espressif's QEMU (ESP-IDF v5.5.5, `idf_tools.py install qemu-xtensa`)
. ~/esp/esp-idf/export.sh
cd firmware/espdet_qemu
idf.py set-target esp32s3 && idf.py build
cd build && python -m esptool --chip esp32s3 merge_bin --fill-flash-size 8MB -o flash.bin @flash_args
qemu-system-xtensa -nographic -machine esp32s3 -m 4M -drive file=flash.bin,if=mtd,format=raw
```

On a board, `idf.py flash monitor` instead (the XIAO ESP32-S3 has octal PSRAM: set `CONFIG_SPIRAM_MODE_OCT=y`;
this build uses quad PSRAM because QEMU emulates only that).

## Status (3 October 2026)

- The program builds (3.6 MB of the 8 MB flash), finds PSRAM, loads the model and runs all eight images in QEMU.
- **The detections in QEMU are wrong** (every box scores 1.000 at the top edge). Espressif's own
  `espressif/cat_detect` model, run the same way on Espressif's example cat photo, also finds nothing in QEMU,
  with and without the S3 AI instructions (ESP-DL rebuilt as plain C), while a single PIE vector instruction
  runs correctly. So QEMU does not reproduce ESP-DL's arithmetic; the on-chip accuracy and speed need a board.
- The 8-bit model's expected detections come from ESP-PPQ (`reference.json`); on the full public test set it
  reaches speed-bump mAP50 0.72 and pothole 0.24 (`ai/results/espdet_pico.md`).
