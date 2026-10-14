"""Backend logic between the API and the trust engine: accept a report, decide, answer hazard queries,
and update device reputation once per confident outcome."""
from __future__ import annotations

import json
import sqlite3

from icvsp.config import Config
from icvsp.consensus import Decision, Engine
from icvsp.geo import along_and_across, distance_m, heading_diff
from icvsp.model import Beacon, HazardState, from_iso
from icvsp.trust import Reputation
from icvsp.validation import schema_errors

from .store import Store
from .verify import Envelope, Rejected, parse, verify_signature

BEACON_WINDOW_S = 2 * 3600   # position history kept in the decision (plausibility and "drove past")


class Service:
    def __init__(self, store: Store, cfg: Config | None = None):
        self.store, self.cfg = store, cfg or Config()

    # ---------- devices ----------
    def register(self, device_id: str, public_key: str, now: float, vetted: bool = False) -> None:
        """Controlled provisioning (FR-SEC-04). `vetted` gives a known fleet device a starting history,
        as if it had 3 confirmed reports; ordinary devices start as new (reliability 0.25)."""
        try:
            bytes.fromhex(public_key)
            if len(public_key) != 64:
                raise ValueError
        except ValueError:
            raise Rejected(400, "public_key must be 64 hex characters (Ed25519)") from None
        try:
            self.store.add_device(device_id, public_key.lower(), now, good=3.0 if vetted else 0.0)
        except sqlite3.IntegrityError:
            raise Rejected(409, "device already registered") from None

    # ---------- reports ----------
    def submit(self, raw: bytes, now: float) -> dict:
        """Returns {"status": "accepted" | "duplicate", ...} or raises Rejected."""
        env: Envelope | None = None
        try:
            env = parse(raw)
            dev = self.store.device(env.sender)
            if dev is None:
                raise Rejected(403, "unknown device")
            if dev["status"] != "ACTIVE":
                raise Rejected(403, "device revoked")
            verify_signature(env, dev["public_key"])
            ev = env.event
            errs = schema_errors(ev)
            if errs:
                raise Rejected(422, "schema: " + "; ".join(errs))
            if ev["device_id"] != env.sender or ev["event_id"] != env.msg_id:
                raise Rejected(422, "event device_id / event_id differ from the signed envelope")
            line = env.line
            old = self.store.report(ev["event_id"])
            if old is not None:
                if old["line"] == line:   # a retry after a timeout: already stored, nothing to do
                    return {"status": "duplicate", "event_id": ev["event_id"]}
                raise Rejected(409, "event_id already used for a different report")
            try:
                self.store.add_report(ev, line, from_iso(ev["detected_at"]), now)
            except sqlite3.IntegrityError:
                raise Rejected(409, f"repeated boot {ev['boot_id']} / sequence {ev['sequence']}") from None
            return {"status": "accepted", "event_id": ev["event_id"]}
        except Rejected as r:
            self.store.reject(now, env.sender if env else None, env.msg_id if env else None, r.reason)
            raise

    def add_beacons(self, beacons: list[dict]) -> int:
        rows = [(b["vehicle_id"], float(b["t"]), float(b["lat"]), float(b["lon"]), float(b["speed_mps"]),
                 float(b["heading_deg"]) % 360, int(bool(b.get("detector_on", True)))) for b in beacons]
        self.store.add_beacons(rows)
        return len(rows)

    # ---------- decisions ----------
    def _engine(self, now: float) -> tuple[Engine, dict[str, str]]:
        rep = Reputation(self.cfg.prior_good, self.cfg.prior_bad)
        for vid, (good, bad) in self.store.reputations().items():
            rep.seed(vid, good, bad)
        eng = Engine(self.cfg, rep)
        for b in self.store.beacons_since(now - BEACON_WINDOW_S):
            eng.ingest_beacon(Beacon(b["vehicle_id"], b["t"], b["lat"], b["lon"], b["speed_mps"],
                                     b["heading_deg"], bool(b["detector_on"])))
        settled = {}
        oldest = now - max(self.cfg.max_age_s.values())
        for r in self.store.reports_since(oldest):
            eng.ingest_dict(json.loads(r["line"])["event"])
            settled[r["event_id"]] = r["settled"]
        return eng, settled

    def decide(self, now: float) -> list[Decision]:
        eng, _ = self._engine(now)
        return eng.decide(now).decisions

    def settle(self, now: float) -> list[str]:
        """Reputation update from confident outcomes, each report and each pass-by counted once."""
        eng, settled = self._engine(now)
        log = []
        for d in eng.decide(now).decisions:
            key = f"{d.type}:{d.lat:.4f}:{d.lon:.4f}"
            contra_groups = len({e.group for e in d.contra})
            confirmed = d.state == HazardState.CONFIRMED
            refuted = d.state == HazardState.SUPPRESSED and (contra_groups >= 2 or d.sybil_suspected)
            if not (confirmed or refuted):
                continue
            for e in d.support:
                if e.event_id and not settled.get(e.event_id):
                    self.store.add_reputation(e.source, 1.0 if confirmed else 0.0, 0.0 if confirmed else 1.0)
                    self.store.mark_settled(e.event_id)
                    log.append(f"{e.source}: report {e.event_id} {'confirmed' if confirmed else 'contradicted'}")
            if confirmed:
                for e in d.contra:
                    if not self.store.pass_settled(e.source, key):
                        self.store.add_reputation(e.source, 0.0, 0.5)
                        log.append(f"{e.source}: missed confirmed {d.hazard_id}")
        return log

    def hazards(self, now: float, lat: float, lon: float, radius_m: float,
                heading: float | None = None) -> list[dict]:
        """Hazards worth showing near a position: advisory (unconfirmed) or warning (corroborated / confirmed).
        With a heading, only hazards in the same direction and still ahead are returned."""
        out = []
        for d in self.decide(now):
            if d.state in (HazardState.SUPPRESSED, HazardState.EXPIRED):
                continue
            dist = distance_m(lat, lon, d.lat, d.lon)
            if dist > radius_m:
                continue
            if heading is not None:
                if heading_diff(heading, d.heading_deg) > self.cfg.heading_tol_deg:
                    continue
                along, _ = along_and_across(lat, lon, d.lat, d.lon, d.heading_deg)
                if along > 0:   # already past it
                    continue
            out.append({
                "hazard_id": d.hazard_id, "type": d.type, "state": d.state.value,
                "level": "advisory" if d.state == HazardState.UNCONFIRMED else "warning", "severity": d.severity,
                "latitude": round(d.lat, 6), "longitude": round(d.lon, 6), "heading_deg": round(d.heading_deg, 1),
                "distance_m": round(dist, 1), "independent_sources": d.independent_sources,
                "net_evidence": round(d.net, 3), "reasons": d.reasons,
            })
        return sorted(out, key=lambda h: h["distance_m"])

    # ---------- operator view ----------
    def overview(self, now: float, recent: int = 30, position_window_s: float = 15.0) -> dict:
        """Everything the operator dashboard shows: devices with their reliability, every current hazard
        decision (including suppressed ones and why), live vehicle positions, latest reports and rejections."""
        eng, _ = self._engine(now)
        result = eng.decide(now)
        rep = Reputation(self.cfg.prior_good, self.cfg.prior_bad)
        devices = []
        for d in self.store.devices():
            rep.seed(d["device_id"], d["good"], d["bad"])
            devices.append({"device_id": d["device_id"], "status": d["status"], "good": d["good"], "bad": d["bad"],
                            "reliability": round(rep.score(d["device_id"]), 3)})
        hazards = [{
            "hazard_id": d.hazard_id, "type": d.type, "state": d.state.value, "action": d.action,
            "severity": d.severity,
            "latitude": round(d.lat, 6), "longitude": round(d.lon, 6), "heading_deg": round(d.heading_deg, 1),
            "independent_sources": d.independent_sources, "net_evidence": round(d.net, 3),
            "support": sorted({e.source for e in d.support}), "contra": len({e.source for e in d.contra}),
            "sybil_suspected": d.sybil_suspected, "reasons": d.reasons,
        } for d in result.decisions]
        reports = []
        for r in self.store.recent_reports(recent):
            ev = json.loads(r["line"])["event"]
            reports.append({"event_id": r["event_id"], "device_id": r["device_id"], "type": ev["type"],
                            "latitude": ev["latitude"], "longitude": ev["longitude"],
                            "detection_confidence": ev["detection_confidence"], "detected_at": r["detected_at"],
                            "received_at": r["received_at"], "settled": bool(r["settled"])})
        return {
            "now": now,
            "counts": {k: self.store.count(k) for k in ("devices", "reports", "rejections")},
            "devices": devices, "hazards": hazards, "reports": reports,
            "rejections": [dict(r) for r in self.store.rejections(recent)][::-1],
            "engine_rejected": [{"event_id": x.event_id, "device_id": x.source, "reasons": x.reasons}
                                for x in result.rejected[-recent:]],
            "vehicles": [{"vehicle_id": b["vehicle_id"], "t": b["t"], "lat": b["lat"], "lon": b["lon"],
                          "heading_deg": b["heading_deg"], "speed_mps": b["speed_mps"]}
                         for b in self.store.latest_positions(now - position_window_s, now)],
        }
