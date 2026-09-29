"""Charts for Stage 2 (matplotlib, only needed for plotting).

Three small panels per sweep, one metric each, same x-axis. Series colours are
fixed per decider (never by rank) and every line is also labelled directly.
"""
from __future__ import annotations

from pathlib import Path

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
SERIES = {  # validated categorical slots 1–3 (light mode)
    "Engine": "#2a78d6",
    "B1 naive": "#eb6834",
    "B2 majority": "#1baf7a",
}
MARKERS = {"Engine": "o", "B1 naive": "s", "B2 majority": "^"}
PANELS = [("false_alert_rate", "False-alert rate", "share of warnings that were wrong", 0.05),
          ("missed_rate", "Missed-hazard rate", "share of real potholes not warned", None),
          ("accuracy", "Accuracy", "share of runs decided correctly", 0.90)]
TITLES = {
    "malicious": "Malicious share (attackers are new identities)",
    "established": "Malicious share (attackers have a good history)",
    "packet_loss": "Packet loss (20% malicious)",
    "gps_noise": "GPS noise σ, metres (20% malicious)",
}


def _x_label(sweep: str, v: float) -> str:
    return f"{v:.0%}" if sweep in ("malicious", "established", "packet_loss") else f"{v:g} m"


def plot_sweep(rows: list[dict], sweep: str, path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sub = [r for r in rows if r["sweep"] == sweep and r["decider"] in SERIES]
    xs = sorted({r["value"] for r in sub})
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.1), facecolor=SURFACE)
    fig.subplots_adjust(left=0.05, right=0.9, top=0.8, bottom=0.17, wspace=0.42)

    for ax, (metric, title, sub_title, target) in zip(axes, PANELS):
        ax.set_facecolor(SURFACE)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=TEXT_2, labelsize=9, length=0)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_ylim(0, 1.02)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
        ax.set_xticks(xs)
        ax.set_xticklabels([_x_label(sweep, v) for v in xs])
        ax.set_title(title, loc="left", fontsize=11.5, color=TEXT, fontweight="bold", pad=18)
        if target is not None:
            ax.axhline(target, color=TEXT_2, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
            sub_title += f"  ·  dashed = target {'≤' if metric == 'false_alert_rate' else '≥'} {target:.0%}"
        ax.text(0, 1.035, sub_title, transform=ax.transAxes, fontsize=8.5, color=TEXT_2)

        ends = []
        for name, color in SERIES.items():
            pts = sorted((r["value"], r[metric]) for r in sub if r["decider"] == name)
            px = [p[0] for p in pts]
            py = [p[1] for p in pts]
            ax.plot(px, py, color=color, linewidth=2, marker=MARKERS[name], markersize=6,
                    markeredgecolor=SURFACE, markeredgewidth=1.5, solid_capstyle="round", zorder=3)
            ends.append([py[-1], name, color])
        # direct labels at the right end, nudged apart so they never collide
        ends.sort()
        for i in range(1, len(ends)):
            if ends[i][0] - ends[i - 1][0] < 0.07:
                ends[i][0] = ends[i - 1][0] + 0.07
        for y, name, color in ends:
            ax.annotate(name, xy=(xs[-1], y), xytext=(8, 0), textcoords="offset points",
                        fontsize=8.5, color=TEXT, va="center")

    runs = int(sub[0]["runs"]) if sub else 0
    fig.suptitle(TITLES.get(sweep, sweep), x=0.05, y=0.97, ha="left", fontsize=13, color=TEXT, fontweight="bold")
    fig.text(0.05, 0.03, f"{runs} simulated runs per point. Simulated data with untuned weights; "
             "shows the mechanism, not real-world performance.", fontsize=8.5, color=TEXT_2)
    path = Path(path)
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return path


def plot_all(rows: list[dict], out: Path) -> list[Path]:
    return [plot_sweep(rows, sw, Path(out) / f"stage2_{sw}.png")
            for sw in TITLES if any(r["sweep"] == sw for r in rows)]
