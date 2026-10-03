"""Check the firmware's signed reports from the backend's side.

Runs the native demo (a simulated drive), then for every report it prints:
  1. the event passes the trust engine's own validation (engine/icvsp/validation.py),
  2. the Ed25519 signature verifies with an independent library (Python `cryptography`),
  3. a tampered copy and a copy with a changed time do not verify.

    cd firmware && pio run -e native && python tools/check_with_engine.py

Needs `pip install cryptography`.
"""
import json, struct, subprocess, sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FW.parent / 'engine'))
from icvsp.validation import schema_errors  # noqa: E402


def signed_bytes(sender: str, msg_id: str, utc_ms: int, payload: bytes) -> bytes:
    """Same layout as firmware/lib/icvsp_core/src/signer.h."""
    return b'ICVSP-v1\0' + sender.encode() + b'\0' + msg_id.encode() + b'\0' + struct.pack('>q', utc_ms) + payload


def verifies(key, sig, *args) -> bool:
    try:
        key.verify(sig, signed_bytes(*args))
        return True
    except InvalidSignature:
        return False


def main():
    program = FW / '.pio/build/native/program'
    lines = subprocess.run([str(program)], capture_output=True, text=True, check=True).stdout.splitlines()
    assert lines, 'the demo printed no reports'
    ok = True
    for line in lines:
        r = json.loads(line)
        # the signed payload is the event exactly as the device wrote it
        payload = line[len('{"event":'): line.index(',"sender":')].encode()
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(r['public_key']))
        sig = bytes.fromhex(r['signature'])
        args = (r['sender'], r['msg_id'], r['utc_ms'])
        ev = r['event']
        errs = schema_errors(ev)
        good = verifies(key, sig, *args, payload)
        tampered = verifies(key, sig, *args, payload.replace(b'"severity":"', b'"severity":"x'))
        retimed = verifies(key, sig, r['sender'], r['msg_id'], r['utc_ms'] + 60000, payload)
        print(f"{ev['event_id']:16} {ev['type']:10} {ev['severity']:6} conf {ev['confidence']:.2f}  "
              f"engine: {'valid' if not errs else '; '.join(errs)}  signature: {'ok' if good else 'BAD'}  "
              f"tampered/retimed rejected: {not tampered and not retimed}")
        ok &= good and not tampered and not retimed and not errs
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
