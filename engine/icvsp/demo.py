"""Command-line demo.

  python -m icvsp.demo                    list scenarios
  python -m icvsp.demo sybil              run one scenario
  python -m icvsp.demo all                run them all
  python -m icvsp.demo sybil --export out/sybil     also write traces.csv + events.jsonl
  python -m icvsp.demo --from out/sybil   run the engine on existing trace files
"""
from __future__ import annotations

import argparse
import sys

from . import tracefile
from .config import Config
from .consensus import Engine
from .model import HazardState
from .pipeline import Result, evaluate
from .risk import targets
from .sim import SCENARIOS, run

USE_COLOR = sys.stdout.isatty()


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


STATE_COLOR = {HazardState.SUPPRESSED: "31", HazardState.UNCONFIRMED: "33",
               HazardState.CORROBORATED: "34", HazardState.CONFIRMED: "32", HazardState.EXPIRED: "90"}


def show(res: Result) -> None:
    s, rep = res.scenario, res.report
    print()
    print(c(f"━━ {s.title} ━━", "1"))
    print(s.description)
    print(f"{len(res.sim.beacons)} beacons, {len(res.sim.events)} Safety Events")

    if rep.rejected:
        print(c("\nRejected before scoring", "1"))
        for r in rep.rejected:
            print(f"  ✗ {r.event_id} from {r.source}: {'; '.join(r.reasons)}")

    if not rep.decisions:
        print(c("\nNo hazard: nothing survived the checks.", "1"))
    for d in rep.decisions:
        print(c(f"\nHazard {d.hazard_id} · {d.type} on {d.road_segment}", "1"))
        print(f"  {'source':<8}{'side':<9}{'conf':>6}{'cons':>6}{'hist':>6}{'fresh':>7}{'weight':>9}")
        for e in d.support + d.contra:
            sign = "+" if e.kind == "support" else "−"
            w = e.weight if e.kind == "support" else e.weight * res.engine.cfg.lam
            print(f"  {e.source:<8}{e.kind:<9}{e.trust.c:>6.2f}{e.trust.g:>6.2f}{e.trust.h:>6.2f}"
                  f"{e.trust.f:>7.2f}{sign + format(w, '.2f'):>9}")
        print(f"  State: {c(d.state.value.upper(), STATE_COLOR[d.state] + ';1')}   "
              f"(net {d.net:+.2f}, {d.independent_sources} independent source(s))")
        print(f"  Action: {d.action}")
        print("  Why:")
        for r in d.reasons:
            print(f"    › {r}")
        warn, skip = res.targets.get(d.hazard_id, ([], []))
        for t in warn:
            eta = f", ETA {t.eta_s:.0f} s" if t.eta_s else ""
            print(c(f"  → {t.level.upper()} to {t.vehicle_id}: {t.distance_m:.0f} m ahead{eta}", "32"))
        for k in skip:
            print(f"  · {k.vehicle_id} not warned: {k.reason}")
        if d.state in (HazardState.SUPPRESSED, HazardState.EXPIRED):
            print(f"  · {s.observer} not warned: hazard {d.state.value}")

    if res.reputation_log:
        print(c("\nReputation updates", "1"))
        for line in res.reputation_log:
            print(f"  {line}")
    cl = res.cloud
    status = (f"online, {len(cl.stored)} items synced" if cl.online
              else f"OFFLINE, {len(cl.queue)} items queued (decision above was made locally)")
    print(c("\nCloud: ", "1") + status)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m icvsp.demo", description="ICVSP trust & consensus engine demo")
    p.add_argument("scenario", nargs="?", help="scenario key, or 'all'")
    p.add_argument("--export", metavar="DIR", help="write traces.csv and events.jsonl for the scenario")
    p.add_argument("--from", dest="src", metavar="DIR", help="run the engine on existing trace files")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args(argv)

    if a.src:
        cfg = Config()
        beacons, events = tracefile.read(a.src)
        eng = Engine(cfg)
        for b in beacons:
            eng.ingest_beacon(b)
        for ev in events:
            eng.ingest_dict(ev)
        now = max([b.t for b in beacons] + [0])
        rep = eng.decide(now)
        for d in rep.decisions:
            warn, _ = targets(d, eng.traces, now, cfg)
            who = ", ".join(t.vehicle_id for t in warn) or "nobody"
            print(f"{d.hazard_id}: {d.state.value.upper()} (net {d.net:+.2f}); warn {who}")
            for r in d.reasons:
                print(f"  › {r}")
        for r in rep.rejected:
            print(f"rejected {r.event_id}: {'; '.join(r.reasons)}")
        print("(reputation history is not stored in trace files, so every source starts as new)")
        return 0

    if not a.scenario:
        print("Scenarios:")
        for k, s in SCENARIOS.items():
            print(f"  {k:<14}{s.title}")
        print("\nRun one with:  python -m icvsp.demo <scenario>   or   python -m icvsp.demo all")
        return 0

    keys = list(SCENARIOS) if a.scenario == "all" else [a.scenario]
    for k in keys:
        if k not in SCENARIOS:
            print(f"unknown scenario '{k}'. Options: {', '.join(SCENARIOS)}, all")
            return 1
        res = evaluate(SCENARIOS[k], seed=a.seed)
        show(res)
        if a.export:
            folder = a.export if len(keys) == 1 else f"{a.export}/{k}"
            tracefile.write(folder, res.sim.beacons, res.sim.events)
            print(f"\nWrote {folder}/traces.csv and {folder}/events.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
