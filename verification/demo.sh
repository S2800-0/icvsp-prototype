#!/usr/bin/env bash
# Test demonstration, one V-cycle level per run (for short screen recordings).
#
#   verification/demo.sh unit          device (C++, PlatformIO + Unity) and trust engine (Python) unit tests
#   verification/demo.sh integration   backend API tests + device -> engine signed-report check
#   verification/demo.sh system        whole chain in simulation, with faults and attackers
#   verification/demo.sh coverage      every level, then the requirement coverage report
#
# The automated level is the same commands on GitHub Actions (.github/workflows/verification.yml).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
LOGS=$(mktemp -d)
PIO="python3 -m platformio"

say() { printf '\n\033[1;36m== %s\033[0m\n\n' "$*"; }
pause() { [ -t 0 ] && read -r -p $'\n(press Enter) ' _ || true; }

unit() {
  say "UNIT 1/2: device firmware (C++), PlatformIO + Unity, run on the laptop"
  (cd firmware && $PIO test -e native -v > "$LOGS/firmware.log" 2>&1) || { tail -30 "$LOGS/firmware.log"; exit 1; }
  grep -E '^test/.*:(PASS|FAIL)' "$LOGS/firmware.log" | sed -E 's#^test/([a-z_]+)/test_main.cpp:[0-9]+:#\1  #'
  tail -1 "$LOGS/firmware.log"
  pause
  say "UNIT 2/2: trust engine (Python unittest)"
  (cd engine && python3 -m unittest -v 2> >(tee "$LOGS/engine.log" >&2))
}

integration() {
  say "INTEGRATION 1/2: backend API (FastAPI test client): signatures, registry, replay, sign-in, rate limits"
  (cd backend && python3 -m unittest -v 2> >(tee "$LOGS/backend.log" | grep -E '\.\.\. |^Ran|^OK|FAIL' >&2))
  pause
  say "INTEGRATION 2/2: reports signed by the device code, checked by the engine with an independent Ed25519 library"
  (cd firmware && python3 tools/check_with_engine.py | tee "$LOGS/integration.log")
}

system() {
  say "SYSTEM: 40 simulated cars, real device code -> phone -> backend -> trust engine (faults and attackers)"
  python3 -m unittest sil/test_sil.py -v 2> >(tee "$LOGS/sil.log" >&2)
}

coverage() {
  unit; integration; system
  say "COVERAGE: requirement -> test -> result (covered = implemented, integrated, tested, passed)"
  python3 verification/check_traceability.py --unittest-log engine="$LOGS/engine.log" \
    --unittest-log backend="$LOGS/backend.log" --unittest-log sil="$LOGS/sil.log" \
    --firmware-log "$LOGS/firmware.log" --integration-log "$LOGS/integration.log" \
    --out "$ROOT/verification/traceability_report.md"
  echo; echo "Full table: verification/traceability_report.md"
}

case "${1:-}" in
  unit|integration|system|coverage) "$1" ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
