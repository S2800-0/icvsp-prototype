"""Stage 2: compare the engine with the two baselines on randomised scenarios.

  python3 -m icvsp.experiment                 # 1000 runs per setting (a few minutes)
  python3 -m icvsp.experiment --runs 200      # quicker
  python3 -m icvsp.experiment --no-plot       # CSV + summary only

Every run is seeded, so the same command gives the same numbers. Results go to
results/: one CSV with every count, a summary.md, and charts.

What counts as a warning
  B1, B2   any positive verdict is a warning
  Engine   CORROBORATED or CONFIRMED is a warning. UNCONFIRMED only sends a
           low-impact advisory; it is reported separately ("Engine + advisories")
           so the comparison doesn't hide it.

Metrics (ground truth = whether the simulated pothole exists)
  false-alert rate   share of issued warnings that were wrong        FP / (TP + FP)
  missed-hazard rate share of real hazards with no warning           FN / (TP + FN)
  accuracy           share of runs decided correctly                 (TP + TN) / N
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .baselines import b1_naive, b2_majority
from .config import Config
from .geo import distance_m, heading_diff
from .model import HazardState
from .pipeline import evaluate_traces
from .sim import LANE_M, random_scenario, run

DECIDERS = ["B1 naive", "B2 majority", "Engine", "Engine + advisories"]
WARN = {HazardState.CORROBORATED, HazardState.CONFIRMED}


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    def add(self, warned: bool, real: bool) -> None:
        if warned and real:
            self.tp += 1
        elif warned:
            self.fp += 1
        elif real:
            self.fn += 1
        else:
            self.tn += 1

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    def metrics(self) -> dict:
        return {
            "false_alert_rate": _ratio(self.fp, self.tp + self.fp),
            "missed_rate": _ratio(self.fn, self.tp + self.fn),
            "accuracy": _ratio(self.tp + self.tn, self.n),
            "false_alarm_prob": _ratio(self.fp, self.fp + self.tn),
        }


def _ratio(a: int, b: int) -> float:
    return a / b if b else float("nan")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def verdicts(scn, out, cfg: Config) -> dict[str, bool]:
    seg = scn.segment
    lat, lon = seg.point(scn.claimed_s, LANE_M)
    heading = seg.heading_deg
    res = evaluate_traces(scn, out, cfg)

    states = [d.state for d in res.report.decisions
              if distance_m(d.lat, d.lon, lat, lon) <= cfg.merge_radius_m * 1.5
              and heading_diff(d.heading_deg, heading) <= cfg.heading_tol_deg]
    return {
        "B1 naive": b1_naive(out.events, lat, lon, heading, cfg),
        "B2 majority": b2_majority(out.events, res.engine.traces, lat, lon, heading, cfg),
        "Engine": any(s in WARN for s in states),
        "Engine + advisories": any(s in WARN or s == HazardState.UNCONFIRMED for s in states),
    }


@dataclass
class Sweep:
    key: str
    title: str
    param: str
    values: list
    fixed: dict = field(default_factory=dict)


SWEEPS = [
    Sweep("malicious", "Share of malicious vehicles (new identities)", "malicious_frac", [0.0, 0.1, 0.2, 0.3, 0.4]),
    Sweep("established", "Share of malicious vehicles (attackers with a good history)", "malicious_frac",
          [0.0, 0.1, 0.2, 0.3, 0.4], {"attacker_history": (20, 2)}),
    Sweep("packet_loss", "Packet loss (20% malicious)", "packet_loss", [0.0, 0.1, 0.2, 0.3], {"malicious_frac": 0.2}),
    Sweep("gps_noise", "GPS noise σ in metres (20% malicious)", "gps_sigma_m", [1.0, 3.0, 5.0], {"malicious_frac": 0.2}),
]


def run_sweep(sw: Sweep, runs: int, seed: int, cfg: Config) -> list[dict]:
    rows = []
    for vi, value in enumerate(sw.values):
        counts = {name: Counts() for name in DECIDERS}
        for i in range(runs):
            rng = random.Random(seed * 1_000_003 + vi * 10_007 + i)
            kwargs = {"n_vehicles": rng.randint(4, 14), **sw.fixed, sw.param: value}
            scn = random_scenario(rng, **kwargs)
            out = run(scn, seed=rng.randrange(1 << 30))
            real = scn.hazard_s is not None
            for name, warned in verdicts(scn, out, cfg).items():
                counts[name].add(warned, real)
        for name, c in counts.items():
            m = c.metrics()
            fa_lo, fa_hi = wilson(c.fp, c.tp + c.fp)
            mr_lo, mr_hi = wilson(c.fn, c.tp + c.fn)
            rows.append({"sweep": sw.key, "param": sw.param, "value": value, "decider": name,
                         "runs": c.n, "tp": c.tp, "fp": c.fp, "tn": c.tn, "fn": c.fn, **m,
                         "false_alert_ci_lo": fa_lo, "false_alert_ci_hi": fa_hi,
                         "missed_ci_lo": mr_lo, "missed_ci_hi": mr_hi})
    return rows


def pct(x: float) -> str:
    return "–" if x != x else f"{100 * x:.1f}%"


def write_summary(path: Path, rows: list[dict], meta: dict) -> None:
    lines = ["# Stage 2 results: engine vs baselines", "",
             f"{meta['runs']} simulated runs per setting, seed {meta['seed']}, "
             f"about half with a real pothole. Detector assumptions: hit rate {meta['tpr']:.0%}, "
             f"false-alarm rate {meta['fpr']:.0%} per honest vehicle. 4–14 vehicles per run.", "",
             "**These are simulated results.** They show how the decision logic behaves under the "
             "simulator's assumptions, not real-world performance. Weights and thresholds are untuned "
             "(see `icvsp/config.py`).", "",
             "- False-alert rate = share of issued warnings that were wrong (target ≤ 5%)",
             "- Missed-hazard rate = share of real potholes that got no warning",
             "- Accuracy = share of runs decided correctly (target ≥ 90%)",
             "- Engine counts only corroborated/confirmed as a warning; "
             "\"Engine + advisories\" also counts single-source advisories", ""]
    for sw in SWEEPS:
        sub = [r for r in rows if r["sweep"] == sw.key]
        if not sub:
            continue
        lines += [f"## {sw.title}", "",
                  "| " + sw.param + " | Decider | False-alert rate (95% CI) | Missed-hazard rate | Accuracy |",
                  "|---|---|---|---|---|"]
        for r in sub:
            ci = "" if r["false_alert_ci_lo"] != r["false_alert_ci_lo"] else \
                f" ({pct(r['false_alert_ci_lo'])}–{pct(r['false_alert_ci_hi'])})"
            lines.append(f"| {r['value']} | {r['decider']} | {pct(r['false_alert_rate'])}{ci} | "
                         f"{pct(r['missed_rate'])} | {pct(r['accuracy'])} |")
        lines.append("")
    path.write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m icvsp.experiment")
    p.add_argument("--runs", type=int, default=1000, help="runs per setting")
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--out", default="results")
    p.add_argument("--sweeps", default=",".join(s.key for s in SWEEPS))
    p.add_argument("--no-plot", action="store_true")
    a = p.parse_args(argv)

    cfg = Config()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted = set(a.sweeps.split(","))
    rows: list[dict] = []
    t0 = time.time()
    for sw in SWEEPS:
        if sw.key in wanted:
            print(f"running {sw.key}: {len(sw.values)} settings × {a.runs} runs …", flush=True)
            rows += run_sweep(sw, a.runs, a.seed, cfg)

    with open(out / "stage2_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    meta = {"runs": a.runs, "seed": a.seed, "tpr": 0.85, "fpr": 0.05, "config": asdict(cfg)}
    (out / "stage2_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    write_summary(out / "summary.md", rows, meta)
    if not a.no_plot:
        from .plot import plot_all
        for pth in plot_all(rows, out):
            print(f"chart: {pth}")
    print(f"done in {time.time() - t0:.0f} s → {out}/stage2_results.csv, {out}/summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
