"""Run one scenario end to end: simulate → engine → targeting → cloud → reputation."""
from __future__ import annotations

from dataclasses import dataclass

from .cloud import CloudSync
from .config import Config
from .consensus import Engine, Report
from .model import Beacon
from .risk import Skipped, Target, targets
from .sim import Scenario, SimOutput, run
from .trust import Reputation


@dataclass
class Result:
    scenario: Scenario
    sim: SimOutput
    engine: Engine
    report: Report
    targets: dict[str, tuple[list[Target], list[Skipped]]]
    reputation_log: list[str]
    cloud: CloudSync


def evaluate(scn: Scenario, cfg: Config | None = None, seed: int = 7) -> Result:
    cfg = cfg or Config()
    out = run(scn, seed)
    return evaluate_traces(scn, out, cfg)


def evaluate_traces(scn: Scenario, out: SimOutput, cfg: Config) -> Result:
    rep = Reputation(cfg.prior_good, cfg.prior_bad)
    for vid, (good, bad) in out.histories.items():
        rep.seed(vid, good, bad)

    engine = Engine(cfg, rep)
    for b in out.beacons:
        engine.ingest_beacon(b)
    for ev in out.events:
        engine.ingest_dict(ev.to_dict())   # go through the schema check like a real message

    report = engine.decide(out.now)        # the real-time decision: no cloud involved
    tg = {d.hazard_id: targets(d, engine.traces, out.now, cfg) for d in report.decisions}

    cloud = CloudSync(online=scn.cloud_online)
    for ev in out.events:
        cloud.publish({"kind": "event", **ev.to_dict()})
    for d in report.decisions:
        cloud.publish({"kind": "decision", "hazard_id": d.hazard_id, "state": d.state.value, "net": d.net})

    rep_log = engine.settle(report)
    return Result(scn, out, engine, report, tg, rep_log, cloud)
