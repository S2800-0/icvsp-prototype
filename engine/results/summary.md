# Stage 2 results: engine vs baselines

2000 simulated runs per setting, seed 2026, about half with a real pothole. Detector assumptions: hit rate 85%, false-alarm rate 5% per honest vehicle. 4–14 vehicles per run.

**These are simulated results.** They show how the decision logic behaves under the simulator's assumptions, not real-world performance. Weights and thresholds are untuned (see `icvsp/config.py`).

- False-alert rate = share of issued warnings that were wrong (target ≤ 5%)
- Missed-hazard rate = share of real potholes that got no warning
- Accuracy = share of runs decided correctly (target ≥ 90%)
- Engine counts only corroborated/confirmed as a warning; "Engine + advisories" also counts single-source advisories

## Share of malicious vehicles (new identities)

| malicious_frac | Decider | False-alert rate (95% CI) | Missed-hazard rate | Accuracy |
|---|---|---|---|---|
| 0.0 | B1 naive | 28.0% (25.7%–30.5%) | 0.0% | 80.9% |
| 0.0 | B2 majority | 0.1% (0.0%–0.6%) | 1.7% | 99.1% |
| 0.0 | Engine | 0.1% (0.0%–0.6%) | 2.1% | 98.9% |
| 0.0 | Engine + advisories | 0.1% (0.0%–0.6%) | 1.0% | 99.5% |
| 0.1 | B1 naive | 43.5% (41.2%–45.9%) | 0.0% | 62.4% |
| 0.1 | B2 majority | 8.6% (7.0%–10.5%) | 7.9% | 91.9% |
| 0.1 | Engine | 0.1% (0.0%–0.6%) | 4.5% | 97.8% |
| 0.1 | Engine + advisories | 0.1% (0.0%–0.6%) | 2.8% | 98.6% |
| 0.2 | B1 naive | 46.9% (44.7%–49.2%) | 0.1% | 55.9% |
| 0.2 | B2 majority | 24.3% (21.8%–27.0%) | 19.2% | 77.5% |
| 0.2 | Engine | 0.5% (0.2%–1.3%) | 7.4% | 96.0% |
| 0.2 | Engine + advisories | 0.7% (0.4%–1.5%) | 4.5% | 97.4% |
| 0.3 | B1 naive | 49.2% (46.9%–51.4%) | 0.5% | 52.0% |
| 0.3 | B2 majority | 41.6% (38.7%–44.6%) | 36.5% | 59.4% |
| 0.3 | Engine | 1.0% (0.5%–1.9%) | 11.2% | 94.0% |
| 0.3 | Engine + advisories | 1.8% (1.2%–2.9%) | 8.6% | 94.9% |
| 0.4 | B1 naive | 50.1% (47.9%–52.3%) | 1.6% | 50.0% |
| 0.4 | B2 majority | 60.5% (57.6%–63.4%) | 57.0% | 38.8% |
| 0.4 | Engine | 2.5% (1.7%–3.8%) | 19.1% | 89.5% |
| 0.4 | Engine + advisories | 3.7% (2.7%–5.2%) | 14.6% | 91.0% |

## Share of malicious vehicles (attackers with a good history)

| malicious_frac | Decider | False-alert rate (95% CI) | Missed-hazard rate | Accuracy |
|---|---|---|---|---|
| 0.0 | B1 naive | 28.0% (25.7%–30.5%) | 0.0% | 80.9% |
| 0.0 | B2 majority | 0.1% (0.0%–0.6%) | 1.7% | 99.1% |
| 0.0 | Engine | 0.1% (0.0%–0.6%) | 2.1% | 98.9% |
| 0.0 | Engine + advisories | 0.1% (0.0%–0.6%) | 1.0% | 99.5% |
| 0.1 | B1 naive | 43.5% (41.2%–45.9%) | 0.0% | 62.4% |
| 0.1 | B2 majority | 8.6% (7.0%–10.5%) | 7.9% | 91.9% |
| 0.1 | Engine | 0.4% (0.2%–1.1%) | 7.7% | 96.0% |
| 0.1 | Engine + advisories | 1.2% (0.7%–2.1%) | 5.6% | 96.7% |
| 0.2 | B1 naive | 46.9% (44.7%–49.2%) | 0.1% | 55.9% |
| 0.2 | B2 majority | 24.3% (21.8%–27.0%) | 19.2% | 77.5% |
| 0.2 | Engine | 3.5% (2.4%–4.9%) | 18.7% | 89.2% |
| 0.2 | Engine + advisories | 4.6% (3.4%–6.2%) | 15.6% | 90.1% |
| 0.3 | B1 naive | 49.2% (46.9%–51.4%) | 0.5% | 52.0% |
| 0.3 | B2 majority | 41.6% (38.7%–44.6%) | 36.5% | 59.4% |
| 0.3 | Engine | 15.2% (12.8%–17.9%) | 35.9% | 76.5% |
| 0.3 | Engine + advisories | 18.1% (15.7%–20.8%) | 30.3% | 77.3% |
| 0.4 | B1 naive | 50.1% (47.9%–52.3%) | 1.6% | 50.0% |
| 0.4 | B2 majority | 60.5% (57.6%–63.4%) | 57.0% | 38.8% |
| 0.4 | Engine | 36.3% (32.7%–39.9%) | 56.5% | 59.5% |
| 0.4 | Engine + advisories | 37.9% (34.6%–41.3%) | 49.3% | 60.0% |

## Packet loss (20% malicious)

| packet_loss | Decider | False-alert rate (95% CI) | Missed-hazard rate | Accuracy |
|---|---|---|---|---|
| 0.0 | B1 naive | 48.5% (46.2%–50.7%) | 0.4% | 53.8% |
| 0.0 | B2 majority | 26.2% (23.6%–28.9%) | 19.3% | 76.5% |
| 0.0 | Engine | 0.0% (0.0%–0.4%) | 8.0% | 96.1% |
| 0.0 | Engine + advisories | 0.3% (0.1%–0.9%) | 5.5% | 97.2% |
| 0.1 | B1 naive | 48.0% (45.7%–50.3%) | 0.5% | 54.9% |
| 0.1 | B2 majority | 22.6% (19.9%–25.6%) | 33.9% | 74.0% |
| 0.1 | Engine | 0.0% (0.0%–0.5%) | 16.3% | 92.0% |
| 0.1 | Engine + advisories | 0.2% (0.1%–0.8%) | 11.2% | 94.5% |
| 0.2 | B1 naive | 46.1% (43.8%–48.4%) | 1.1% | 57.4% |
| 0.2 | B2 majority | 19.3% (16.4%–22.5%) | 47.4% | 70.1% |
| 0.2 | Engine | 0.6% (0.2%–1.4%) | 28.6% | 85.5% |
| 0.2 | Engine + advisories | 0.5% (0.2%–1.3%) | 23.4% | 88.1% |
| 0.3 | B1 naive | 45.4% (43.1%–47.7%) | 1.4% | 58.6% |
| 0.3 | B2 majority | 15.0% (11.9%–18.6%) | 62.8% | 65.5% |
| 0.3 | Engine | 0.0% (0.0%–0.7%) | 45.5% | 77.4% |
| 0.3 | Engine + advisories | 0.0% (0.0%–0.6%) | 38.8% | 80.8% |

## GPS noise σ in metres (20% malicious)

| gps_sigma_m | Decider | False-alert rate (95% CI) | Missed-hazard rate | Accuracy |
|---|---|---|---|---|
| 1.0 | B1 naive | 48.5% (46.2%–50.7%) | 0.4% | 53.8% |
| 1.0 | B2 majority | 26.2% (23.6%–28.9%) | 19.3% | 76.5% |
| 1.0 | Engine | 0.0% (0.0%–0.4%) | 8.0% | 96.1% |
| 1.0 | Engine + advisories | 0.3% (0.1%–0.9%) | 5.5% | 97.2% |
| 3.0 | B1 naive | 48.5% (46.2%–50.7%) | 0.2% | 54.0% |
| 3.0 | B2 majority | 24.9% (22.4%–27.6%) | 18.6% | 77.7% |
| 3.0 | Engine | 0.4% (0.2%–1.1%) | 7.8% | 96.0% |
| 3.0 | Engine + advisories | 0.5% (0.2%–1.3%) | 5.6% | 97.0% |
| 5.0 | B1 naive | 46.9% (44.6%–49.1%) | 0.1% | 56.0% |
| 5.0 | B2 majority | 24.2% (21.7%–26.9%) | 20.9% | 77.0% |
| 5.0 | Engine | 0.3% (0.1%–1.0%) | 10.7% | 94.5% |
| 5.0 | Engine + advisories | 0.6% (0.3%–1.4%) | 7.9% | 95.8% |
