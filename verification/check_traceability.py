"""Requirement -> test -> result traceability check (V-cycle, right side).

Reads the test logs written by CI (or by a local run), the test list in traceability.csv and the requirement
list in requirements.csv, and
  1. fails if a test in the code is not in traceability.csv, or a listed test no longer exists,
  2. fails if a listed test failed or did not run,
  3. fails if a test names a requirement ID that is not in requirements.csv,
  4. writes a report: every requirement with its tests and an overall result.

    (cd engine && python3 -m unittest -v 2> ../engine.log)
    (cd backend && python3 -m unittest -v 2> ../backend.log)
    python3 -m unittest sil/test_sil.py -v 2> sil.log
    (cd firmware && pio test -e native -v > ../firmware.log && python3 tools/check_with_engine.py > ../integration.log)
    python3 verification/check_traceability.py --unittest-log engine=engine.log --unittest-log backend=backend.log \
        --unittest-log sil=sil.log --firmware-log firmware.log --integration-log integration.log

Experiments (E-) are long or need private data, and inspections (I-) are done by a person, so they are not
rerun here; the report shows them as "recorded".
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def code_tests() -> set[tuple[str, str]]:
    """(file, test) pairs that exist in the code."""
    found = set()
    py = [*(ROOT / 'engine/tests').glob('test_*.py'), *(ROOT / 'backend/tests').glob('test_*.py'),
          *(ROOT / 'sil').glob('test_*.py')]
    for f in sorted(py):
        cls = None
        for line in f.read_text().splitlines():
            if m := re.match(r'class (\w+)', line):
                cls = m.group(1)
            elif m := re.match(r'    def (test_\w+)', line):
                found.add((str(f.relative_to(ROOT)), f'{cls}.{m.group(1)}'))
    for f in sorted((ROOT / 'firmware/test').glob('*/test_main.cpp')):
        for name in re.findall(r'RUN_TEST\((\w+)\)', f.read_text()):
            found.add((str(f.relative_to(ROOT)), name))
    return found


def results(unittest_logs: dict[str, str], firmware_log: str, integration_log: str | None) -> dict[tuple[str, str], str]:
    res = {}
    # "test_x (tests.module.Class.test_x) ... ok"; a test with a docstring prints the docstring on a second line.
    # Each log comes with the folder its tests were run from (engine, backend) or live in (sil).
    for base, log in unittest_logs.items():
        # a warning printed by the code under test can split "... ok" over several lines
        for m in re.finditer(r'^(test_\w+) \(([\w.]+)\.(\w+)\.\w+\)(?:\n[^\n]*?)? \.\.\. '
                             r'(?:[^\n]*\n){0,6}?[^\n]*?\b(ok|FAIL|ERROR|skipped)\b', log, re.M):
            name, module, cls, outcome = m.groups()
            parts = module.split('.')
            if parts[0] == base:
                parts = parts[1:]
            res[(f'{base}/{"/".join(parts)}.py', f'{cls}.{name}')] = 'Pass' if outcome == 'ok' else 'Fail'
    for m in re.finditer(r'^test/(\w+)/test_main\.cpp:\d+:(\w+):(PASS|FAIL|IGNORE)', firmware_log, re.M):
        suite, name, outcome = m.groups()
        res[(f'firmware/test/{suite}/test_main.cpp', name)] = 'Pass' if outcome == 'PASS' else 'Fail'
    if integration_log is not None:
        lines = [l for l in integration_log.splitlines() if 'signature:' in l]
        ok = bool(lines) and all('engine: valid' in l and 'signature: ok' in l and l.rstrip().endswith('True')
                                 for l in lines)
        res[('firmware/tools/check_with_engine.py', '*')] = 'Pass' if ok else 'Fail'
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--unittest-log', action='append', required=True, metavar='FOLDER=LOG',
                    help='Python unittest -v output, e.g. engine=engine.log (repeat for each suite)')
    ap.add_argument('--firmware-log', required=True)
    ap.add_argument('--integration-log')
    ap.add_argument('--out', default='traceability_report.md')
    a = ap.parse_args()

    reqs = {r['id']: r for r in csv.DictReader(open(HERE / 'requirements.csv'))}
    items = list(csv.DictReader(open(HERE / 'traceability.csv')))
    logs = {k: Path(v).read_text() for k, v in (x.split('=', 1) for x in a.unittest_log)}
    res = results(logs, Path(a.firmware_log).read_text(),
                  Path(a.integration_log).read_text() if a.integration_log else None)
    errors = []

    kind = lambda i: i['test_id'].split('-')[0]
    listed = {(i['file'], i['test']) for i in items if kind(i) in ('T', 'F', 'B', 'S')}
    existing = code_tests()
    for f, t in sorted(existing - listed):
        errors.append(f'test not in traceability.csv: {f} {t}')
    for f, t in sorted(listed - existing):
        errors.append(f'listed test no longer exists: {f} {t}')

    by_req = defaultdict(list)
    for i in items:
        k = kind(i)
        if k in ('T', 'F', 'B', 'S'):
            i['result'] = res.get((i['file'], i['test']), 'Not run')
        elif k == 'IT':
            i['result'] = res.get((i['file'], '*'), 'Not run')
        else:
            i['result'] = 'Recorded'
        if i['result'] in ('Fail', 'Not run'):
            errors.append(f"{i['test_id']} {i['test']}: {i['result']}")
        for r in i['requirements'].split():
            if r.startswith('('):
                continue
            if r not in reqs:
                errors.append(f"{i['test_id']} names unknown requirement {r}")
            by_req[r].append(i)

    def verdict(tests):
        if not tests:
            return 'Not verified'
        if any(t['result'] == 'Fail' for t in tests):
            return 'Fail'
        if all(t['result'] == 'Recorded' for t in tests):
            return 'Recorded (experiment / inspection)'
        return 'Pass'

    counts = defaultdict(lambda: defaultdict(int))
    rows = []
    for rid, r in reqs.items():
        v = verdict(by_req.get(rid, []))
        counts[r['set']][v] += 1
        ids = ', '.join(t['test_id'] for t in by_req.get(rid, []))
        rows.append(f"| {rid} | {r['priority']} | {v} | {ids} |")

    n_auto = sum(1 for i in items if kind(i) in ('T', 'F', 'B', 'S', 'IT'))
    n_pass = sum(1 for i in items if i['result'] == 'Pass')
    lines = ['# ICVSP traceability report', '',
             f'{n_pass} of {n_auto} automated verification items pass; '
             f"{sum(1 for i in items if kind(i) == 'E')} experiments and "
             f"{sum(1 for i in items if kind(i) == 'I')} inspections recorded.", '',
             '| Requirement set | Pass | Recorded (experiment / inspection) | Fail | Not verified |', '|---|---|---|---|---|']
    for s in ('system', 'security'):
        c = counts[s]
        lines.append(f"| {s} | {c['Pass']} | {c['Recorded (experiment / inspection)']} | {c['Fail']} | {c['Not verified']} |")
    lines += ['', '| Requirement | Priority | Result | Verified by |', '|---|---|---|---|', *rows]
    if errors:
        lines += ['', '## Problems', '', *[f'- {e}' for e in errors]]
    Path(a.out).write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines[:8]))
    for e in errors:
        print('ERROR:', e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
