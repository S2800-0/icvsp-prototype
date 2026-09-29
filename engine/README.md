# ICVSP trust & consensus engine (prototype)

The decision layer of the Intelligent Connected Vehicle Safety Platform: it takes
Safety Events and position beacons from vehicles and decides, with a written
reason, whether a hazard is real enough to warn anyone, and who.

Plain Python 3.10+, no dependencies.

## Run it

```bash
cd engine
python3 -m icvsp.demo                 # list scenarios
python3 -m icvsp.demo sybil           # run one
python3 -m icvsp.demo all             # run all seven
python3 -m unittest -v                # 39 tests, including every scenario's expected outcome
```

Export a scenario to trace files and run the engine on the files only:

```bash
python3 -m icvsp.demo sybil --export out/sybil
python3 -m icvsp.demo --from out/sybil
```

## Scenarios

| Key | What happens | Expected |
|---|---|---|
| `single` | One trusted vehicle reports; D approaching | Unconfirmed → advisory to D |
| `duplicates` | A, B, C report the same pothole; F in opposite lane | Confirmed → warning to D only |
| `false_report` | A reports a pothole that isn't there; B, C pass and see nothing | Suppressed; A's reputation drops |
| `sybil` | One attacker as five new identities on one trajectory | Suppressed; S1–S5 counted as one |
| `cloud_outage` | Same as duplicates, AWS offline | Same decision; cloud items queued |
| `replay` | A three-day-old genuine report is re-broadcast | Rejected as stale |
| `gps_spoof` | M reports a place its own beacons never reached | Suppressed; consistency 0.3 |

## How a decision is made

1. **Schema check** (`validation.schema_errors`, mirrors `schema/safety_event.schema.json`).
2. **Plausibility** (`validation.plausibility`): future/stale timestamps, GPS accuracy,
   impossible speed, report location vs the sender's own beacons, trace jumps.
   Each check rejects the event or lowers its consistency *g*. *(Data-centric
   misbehaviour detection, van der Heijden et al. 2019.)*
3. **Trust weight** (`trust.py`): `weight = c · g · h · f`
   - *c* detection confidence, *g* consistency,
   - *h* Beta reputation of the source, `(good + 1) / (good + bad + 4)`, so new identities start at 0.25
     *(Jøsang & Ismail 2002)*,
   - *f* freshness `e^(−age/τ)`.
4. **Merge** reports of the same type within 10 m and the same direction into one hazard.
5. **Negative evidence**: vehicles whose beacons cross the spot (same direction, detector on)
   without reporting count against it, weighted by λ = 0.8.
6. **Independence**: identities whose traces stay within 2 m of each other are one source
   (union-find). *Without a certifying authority, Sybil identities can't be prevented, only
   detected and discounted (Douceur 2002).*
7. **State**: `net = Σ support − λ · Σ contra`, counted once per independent group.
   - ≥ 2 sources and net ≥ 1.2 → **confirmed**; ≥ 2 and ≥ 0.6 → **corroborated**
   - net ≥ 0.3 → **unconfirmed** (advisory only); otherwise **suppressed**
   **Cold start (SN-09):** a new identity's weight is at most 0.25, below the 0.3 advisory
   threshold, so a lone early user could never warn anyone. `Engine._cold_start` allows one
   exception: a single new source with confidence ≥ 0.8, consistency ≥ 0.8, no contrary evidence
   and no Sybil suspicion raises an **advisory only**, never a warning. Set
   `Config.cold_start_advisory = False` to disable it. Tested in `tests/test_cold_start.py`.
   The Stage 2 sweeps do not exercise this rule (every honest vehicle has a history and 4–14
   vehicles share the road), so their results are unchanged; a low-density sweep is still to do.
8. **Targeting** (`risk.py`): warn vehicles in the same direction, behind the hazard, within 500 m.
9. **Reputation update** (`Engine.settle`): only after confident outcomes.

Every number above is in `icvsp/config.py`. They are starting guesses, not tuned values.

## Files

```
schema/safety_event.schema.json   the message format
icvsp/config.py       all tunable parameters
icvsp/validation.py   schema + plausibility checks
icvsp/trust.py        trust weight, freshness, Beta reputation
icvsp/consensus.py    the engine: merge, negative evidence, independence, state, reasons
icvsp/risk.py         which vehicles to warn
icvsp/cloud.py        stand-in for AWS (the engine never imports it)
icvsp/sim.py          small road simulator + the seven scenarios + random scenarios
icvsp/tracefile.py    traces.csv / events.jsonl reader and writer
icvsp/pipeline.py     simulate → decide → target → cloud → reputation
icvsp/demo.py         command-line demo
icvsp/baselines.py    B1 naive forwarding, B2 majority vote
icvsp/experiment.py   Stage 2 runner (metrics, confidence intervals, summary)
icvsp/plot.py         Stage 2 charts
results/              Stage 2 output (CSV, summary.md, charts)
tests/                39 tests
```

## Trace format (the SUMO swap point)

`traces.csv`: `vehicle_id,t,lat,lon,speed_mps,heading_deg,detector_on`, one row per beacon.
`events.jsonl`: one Safety Event per line.

Anything that writes these two files can drive the engine. SUMO's floating-car-data
output (`--fcd-output`) has vehicle id, time, position, speed and angle per step, so a
small converter is all that's needed to move to SUMO traffic in weeks 7–9.

## Known limitations (say these out loud)

- The simulator is a straight road with constant speeds; it exercises the logic, not traffic realism.
- The honest detector's hit and false-alarm rates are assumptions until the pothole MVP is measured.
- Negative evidence assumes vehicles broadcast beacons with a "detector on" flag. With packet
  loss, a lost report can look like "saw nothing". This is a real design question for the protocol.
- The independence check only catches identities that share a trajectory. A coordinated
  attack by genuinely separate vehicles is not detected.
- Weights, λ and thresholds are hand-picked. Comparing against Bayesian or Dempster–Shafer
  combination (Raya et al. 2008) is the natural next experiment.

## Stage 2: engine vs baselines

```bash
python3 -m icvsp.experiment              # 2000 runs per point takes ~1 min: --runs 2000
python3 -m icvsp.experiment --runs 200   # quick check
```

Every run is seeded, so the same command reproduces the same numbers. Output in `results/`:
`stage2_results.csv` (every count, with 95% Wilson intervals), `summary.md` (tables),
`stage2_meta.json` (the exact config used) and one chart per sweep.

Three deciders see exactly the same messages:

- **B1 naive**: warn if anyone reported it.
- **B2 majority**: among vehicles that drove over the spot, warn if more reported it than didn't.
  Every identity gets one vote.
- **Engine**: warns only on corroborated or confirmed. Single-source advisories are reported
  separately as "Engine + advisories".

Each run has 4–14 vehicles and a pothole that is real about half the time. Honest detectors
have an assumed 85% hit rate and 5% false-alarm rate. When the pothole is real, attackers stay
silent to hide it; when it isn't, they fabricate it with false reports, Sybil identities or
position spoofing.

### Findings (2000 runs per point)

| Setting | Engine false-alert rate | Engine accuracy | B2 false-alert rate | B1 false-alert rate |
|---|---|---|---|---|
| No attackers | 0.1% | 98.9% | 0.1% | 28.0% |
| 20% malicious, new identities | 0.5% | 96.0% | 24.3% | 46.9% |
| 40% malicious, new identities | 2.5% | 89.5% | 60.5% | 50.1% |
| 20% malicious, attackers with good history | 3.5% | 89.2% | 24.3% | 46.9% |
| 40% malicious, attackers with good history | 36.3% | 59.5% | 60.5% | 50.1% |

1. **Some form of consensus is needed even with no attackers.** Honest detectors' occasional
   false alarms add up, so B1 is wrong on 28% of the warnings it issues.
2. **Against new identities, the engine meets the ≤ 5% false-alert target up to 40% malicious**
   (accuracy stays at or above 89.5%).
   B2 collapses because Sybil identities outvote honest vehicles.
3. **Against attackers with a good history, the engine holds the false-alert target only up to
   about 20% malicious** (3.5%, with accuracy just under 90%). Much of the protection comes from reputation, so insider or long-lived attackers
   are the real open problem. That makes stronger data-centric checks the natural research
   direction.
4. **Caution costs missed hazards.** At 20% malicious (new identities), the engine misses 7.4% of
   real potholes, against about 0% for B1. Counting advisories lowers this to 4.5%. The balance
   is a design choice worth discussing with mentors.
5. **Packet loss is the biggest weakness measured.** False alerts stay near 0%, but missed hazards
   rise to 45% at 30% loss, because a lost report looks like "drove past and saw nothing". This
   confirms the limitation noted above and points to a protocol change (e.g. vehicles re-announce
   what they reported in their beacons).
6. **GPS noise up to 5 m is handled.** Missed hazards stay at 10.7%. The first run of this
   experiment found a bug that pushed this to 45%: noisy reports of one pothole split into two
   clusters, and each reporter then counted as "saw nothing" against the other. Fixed, with a
   regression test (`tests/test_stage2.py`).

**Caveats.** Straight road, constant speeds, assumed detector rates, simple attacker behaviour,
untuned weights. These results show how the logic behaves under the simulator's assumptions, not
real-world performance. The next steps are measured detector rates from the pothole MVP, SUMO
traffic, tuning on a separate validation seed, and comparing against a Bayesian or
Dempster–Shafer combination.

## References

- Raya, Papadimitratos, Gligor, Hubaux. On Data-Centric Trust Establishment in Ephemeral Ad Hoc Networks. IEEE INFOCOM 2008. doi:10.1109/INFOCOM.2008.180
- van der Heijden, Dietzel, Leinmüller, Kargl. Survey on Misbehavior Detection in Cooperative Intelligent Transportation Systems. IEEE COMST 2019. doi:10.1109/COMST.2018.2873088
- Jøsang, Ismail. The Beta Reputation System. 15th Bled Electronic Commerce Conference, 2002.
- Douceur. The Sybil Attack. IPTPS 2002, LNCS. doi:10.1007/3-540-45748-8_24
- van der Heijden, Lukaseder, Kargl. VeReMi. SecureComm 2018. doi:10.1007/978-3-030-01701-9_18
- Kamel et al. VeReMi Extension. IEEE ICC 2020. doi:10.1109/ICC40277.2020.9149132
