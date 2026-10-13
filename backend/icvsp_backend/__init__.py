"""ICVSP backend prototype: receives signed device reports from phones, verifies them, runs the trust engine
and answers hazard queries. See backend/README.md."""
import sys
from pathlib import Path

# the trust engine lives in engine/ of the same repository
_ENGINE = Path(__file__).resolve().parents[2] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.append(str(_ENGINE))
