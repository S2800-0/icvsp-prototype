#!/usr/bin/env bash
# Run the whole V-cycle locally, exactly as CI does (.github/workflows/verification.yml), and write the evidence to
# verification/out/: JUnit XML per level, an HTML code-coverage report and the requirement traceability report.
#
#   verification/run_all.sh          then open verification/out/coverage/index.html
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=verification/out
rm -rf "$OUT" && mkdir -p "$OUT"

echo "== Unit: device firmware (PlatformIO + Unity)"
(cd firmware && python3 -m platformio test -e native --junit-output-path "../$OUT/firmware.xml")

echo "== Unit, integration and system: trust engine, backend API, simulation (pytest + coverage)"
python3 -m pytest -v --junitxml="$OUT/python.xml" --cov=engine/icvsp --cov=backend/icvsp_backend \
  --cov-report=term --cov-report=html:"$OUT/coverage"

echo "== Integration: device-signed reports checked by the engine (independent Ed25519 library)"
(cd firmware && python3 tools/check_with_engine.py) | tee "$OUT/integration.log"

echo "== Traceability: requirement -> test -> result"
python3 verification/check_traceability.py --junit "$OUT/firmware.xml" --junit "$OUT/python.xml" \
  --integration-log "$OUT/integration.log" --out "$OUT/traceability_report.md"
