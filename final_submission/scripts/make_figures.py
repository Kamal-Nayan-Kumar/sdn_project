#!/usr/bin/env python3
"""Build every figure the paper uses, from the recorded experiment data.

The script reads only `final_submission/data/` -- it never talks to the lab
and never re-measures anything -- so a figure can always be traced back to
the JSON/CSV that produced it. Each figure is written as both PDF (for the
paper) and PNG (for previewing).

Usage:
    make_figures.py --data-dir final_submission/data \
                    --figure-dir final_submission/paper/figures
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

# A restrained, colour-blind-safe palette that survives greyscale printing.
INK = "#111827"
MUTED = "#5A6472"
BLUE = "#2563EB"
CYAN = "#0891B2"
RED = "#DC2626"
AMBER = "#B45309"
GREEN = "#059669"
GREY = "#D7DBE0"

plt.rcParams.update({
    "font.family": "DejaVu Serif",
    "font.size": 8.5,
    "axes.labelsize": 8.5,
    "axes.titlesize": 9.0,
    "axes.edgecolor": MUTED,
    "axes.linewidth": 0.7,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5,
    "legend.fontsize": 7.5,
    "legend.frameon": False,
    "figure.dpi": 200,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
})


def load_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_csv(path: str) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def save(fig: plt.Figure, out_dir: str, name: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(out_dir, f"{name}.{ext}"))
    plt.close(fig)
    print(f"    {name}.pdf / {name}.png")


# ---------------------------------------------------------------------------
# Figure 1: the topology, drawn from the lab's own description of it
# ---------------------------------------------------------------------------

def figure_topology(topology: dict[str, Any], out_dir: str) -> None:
    if not topology:
        print("    (skipping topology figure: no topology.json)")
        return

    nodes = {n["id"]: n for n in topology.get("nodes", [])}
    links = topology.get("links", [])

    # The layout mirrors the physical fabric: the hosts hang off the left and
    # right edges, and the four switches form a ring in the middle.
    pos = {
        "h1": (0.55, 2.85), "h2": (0.55, 1.35),
        "h3": (5.45, 2.85), "h4": (5.45, 1.35),
        "s1": (1.75, 2.10), "s2": (3.00, 2.75), "s3": (3.00, 1.45),
        "s4": (4.25, 2.10),
    }

    fig, ax = plt.subplots(figsize=(3.5, 2.35))
    congested = set()
    for link in links:
        a, b = link.get("source"), link.get("target")
        if a not in pos or b not in pos:
            continue
        is_access = link.get("kind") == "access"
        if link.get("congestible"):
            congested.add(link.get("id"))
        ax.plot([pos[a][0], pos[b][0]], [pos[a][1], pos[b][1]],
                color=GREY if is_access else "#B9C0C9",
                linewidth=0.8 if is_access else 1.2, zorder=1)
        if link.get("congestible"):
            ax.plot([pos[a][0], pos[b][0]], [pos[a][1], pos[b][1]],
                    color=RED, linewidth=1.6, linestyle=(0, (4, 2)), zorder=2)

    # The two routes the agent chooses between.
    ax.plot([pos["s1"][0], pos["s2"][0], pos["s4"][0]],
            [pos["s1"][1], pos["s2"][1], pos["s4"][1]],
            color=BLUE, linewidth=1.6, zorder=3)
    ax.plot([pos["s1"][0], pos["s3"][0], pos["s4"][0]],
            [pos["s1"][1], pos["s3"][1], pos["s4"][1]],
            color=CYAN, linewidth=1.6, zorder=3)

    for node_id, node in nodes.items():
        if node_id not in pos:
            continue
        x, y = pos[node_id]
        if node.get("kind") == "switch":
            ax.add_patch(plt.Circle((x, y), 0.21, facecolor="white",
                                    edgecolor=INK, linewidth=1.1, zorder=4))
            ax.text(x, y, node_id, ha="center", va="center", fontsize=7.0,
                    color=INK, zorder=5)
        else:
            ax.add_patch(FancyBboxPatch((x - 0.30, y - 0.18), 0.60, 0.36,
                                         boxstyle="round,pad=0.02",
                                         facecolor="white", edgecolor=MUTED,
                                         linewidth=0.9, zorder=4))
            ax.text(x, y, node_id, ha="center", va="center", fontsize=7.0,
                    color=INK, zorder=5)

    # Route labels, placed clear of the nodes and of each other.
    ax.text(2.22, 2.22, "$s1$-$s2$-$s4$", fontsize=6.4, color=BLUE,
            ha="center", va="top")
    ax.text(2.05, 1.66, "$s1$-$s3$-$s4$", fontsize=6.4, color=CYAN,
            ha="center", va="top")
    ax.text(3.78, 2.56, "$s2$-$s4$ degraded", fontsize=6.4, color=RED,
            ha="center", va="bottom")
    ax.text(0.55, 3.22, "video source", fontsize=6.0, color=MUTED,
            ha="center")
    ax.text(5.45, 3.22, "voice", fontsize=6.0, color=MUTED, ha="center")
    ax.text(4.25, 0.72, "video sink", fontsize=6.0, color=MUTED, ha="center")

    # The protected intent, drawn behind the fabric so it never hides a node.
    ax.annotate("", xy=pos["h4"], xytext=pos["h1"],
                arrowprops=dict(arrowstyle="-|>", color=INK, linewidth=1.0,
                                connectionstyle="arc3,rad=0.32", shrinkA=20,
                                shrinkB=20),
                zorder=0)

    ax.set_xlim(0.0, 6.0)
    ax.set_ylim(0.20, 3.55)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("The lab topology, from the lab's own description", pad=0,
                 fontsize=8.0)
    save(fig, out_dir, "fig_topology")


# ---------------------------------------------------------------------------
# Figure 2: the agent workflow
# ---------------------------------------------------------------------------

def figure_workflow(out_dir: str) -> None:
    """The agent's state graph, drawn from `agents/graph.py`.

    Left column: the nine nodes of the main flow. Right column: the five
    terminal outcomes, each labelled with the condition that selects it.
    Every edge of the compiled graph is either a downward arrow inside the
    left column or one of the five labelled branches to the right.
    """
    fig, ax = plt.subplots(figsize=(3.5, 3.9))
    ax.set_xlim(0, 9.0)
    ax.set_ylim(0, 10.4)
    ax.axis("off")

    def box(x, y, w, h, text, edge, fill, size=7.0):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                                    boxstyle="round,pad=0.02",
                                    facecolor=fill, edgecolor=edge,
                                    linewidth=1.0, zorder=3))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=size, color=INK, zorder=4, linespacing=1.35)

    def arrow(x1, y1, x2, y2, colour=MUTED, style="-"):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
                                     arrowstyle="-|>", mutation_scale=6.5,
                                     linewidth=0.9, color=colour,
                                     linestyle=style, zorder=2,
                                     shrinkA=0, shrinkB=0))

    # --- the main flow ---------------------------------------------------
    cx, w, h = 0.25, 3.45, 0.78
    steps = [
        ("intent", BLUE, "#EAF0FE"),
        ("observe", BLUE, "#EAF0FE"),
        ("evidence  (Gate 1)", AMBER, "#FDF3E3"),
        ("diagnose", BLUE, "#EAF0FE"),
        ("validate  (Gate 2)", AMBER, "#FDF3E3"),
        ("execute", BLUE, "#EAF0FE"),
        ("observe_after", BLUE, "#EAF0FE"),
        ("verify  (Gate 3)", AMBER, "#FDF3E3"),
        ("finish", BLUE, "#EAF0FE"),
    ]
    ys = [9.15 - i * 0.97 for i in range(len(steps))]
    for (label, edge, fill), y in zip(steps, ys):
        box(cx, y, w, h, label, edge, fill)
    for i in range(len(steps) - 1):
        arrow(cx + w / 2, ys[i], cx + w / 2, ys[i + 1] + h)

    # --- the terminal outcomes -------------------------------------------
    tx, tw = 5.35, 3.40
    terminals = [
        (ys[0], "stop\n(clarify)", MUTED, "#EFF1F4"),
        (ys[2], "satisfied\n(intent met)", GREEN, "#E8F6F0"),
        (ys[2] - 0.97, "abstain\n(evidence short)", RED, "#FDECEC"),
        (ys[4], "held\n(approval needed)", AMBER, "#FDF3E3"),
        (ys[4] - 0.97, "rejected\n(a check failed)", RED, "#FDECEC"),
    ]
    for y, text, edge, fill in terminals:
        box(tx, y, tw, h, text, edge, fill, size=6.4)

    # --- the five branches -----------------------------------------------
    arrow(cx + w, ys[0] + h / 2, tx, ys[0] + h / 2, MUTED, (0, (3, 2)))
    arrow(cx + w, ys[2] + h / 2, tx, ys[2] + h / 2, GREEN, (0, (3, 2)))
    arrow(cx + w, ys[2] + 0.18, tx, ys[2] - 0.97 + h / 2, RED, (0, (3, 2)))
    arrow(cx + w, ys[4] + h / 2, tx, ys[4] + h / 2, AMBER, (0, (3, 2)))
    arrow(cx + w, ys[4] + 0.18, tx, ys[4] - 0.97 + h / 2, RED, (0, (3, 2)))

    # --- headings and footer ---------------------------------------------
    ax.text(cx + w / 2, 10.15, "main flow", fontsize=7.0, color=MUTED,
            ha="center")
    ax.text(tx + tw / 2, 10.15, "terminal outcomes", fontsize=7.0,
            color=MUTED, ha="center")
    ax.text(4.5, 0.30, "every terminal outcome leads to END",
            fontsize=6.4, color=MUTED, ha="center", style="italic")
    ax.text(4.5, 0.00, "a successful command is never a successful recovery",
            fontsize=6.4, color=MUTED, ha="center", style="italic")
    save(fig, out_dir, "fig_workflow")


# ---------------------------------------------------------------------------
# Figure 3: measured latency per phase
# ---------------------------------------------------------------------------

PHASE_ORDER = ["baseline_healthy", "congestion_recovery", "congestion_partial",
               "healthy_impossible_target", "guardrail_blocked", "lab_offline",
               "offered_load_congestion", "degraded_no_oracle",
               "after_restart"]
PHASE_LABEL = {
    "baseline_healthy": "S1",
    "congestion_recovery": "S2\u00d75",
    "congestion_partial": "S3\u00d75",
    "healthy_impossible_target": "S4",
    "guardrail_blocked": "S5",
    "offered_load_congestion": "S7\u00d75",
    "degraded_no_oracle": "S8\u00d75",
    "after_restart": "S1\u2032",
}


def figure_latency_phases(measurements: list[dict[str, str]], out_dir: str) -> None:
    rows = [r for r in measurements if r.get("flow_key") == "h1->h4"]
    if not rows:
        print("    (skipping latency figure: no measurements)")
        return

    phases = [p for p in PHASE_ORDER if any(r.get("phase") == p for r in rows)]
    fig, ax = plt.subplots(figsize=(3.4, 2.4))

    for i, phase in enumerate(phases):
        values = [fnum(r["latency_ms"]) for r in rows if r.get("phase") == phase]
        if not values:
            continue
        jitter = [(i - 0.5) * 0.16 + ((k % 5) - 2) * 0.022
                  for k in range(len(values))]
        ax.scatter(jitter, values, s=13, zorder=4,
                   color=BLUE if "congestion" in phase else CYAN,
                   edgecolor="white", linewidth=0.4)
        ax.plot([i - 0.28, i + 0.28], [statistics.fmean(values)] * 2,
                color=INK, linewidth=1.2, zorder=5)

    ax.axhline(50.0, color=RED, linewidth=1.0, linestyle=(0, (4, 2)))
    ax.text(len(phases) - 0.5, 50.0, " 50 ms intent", color=RED, fontsize=6.8,
            va="bottom", ha="right")

    ax.set_xticks(range(len(phases)))
    ax.set_xticklabels([PHASE_LABEL.get(p, p) for p in phases],
                       rotation=30, ha="right", fontsize=7.0)
    ax.set_ylabel("measured video latency (ms)")
    ax.set_yscale("log")
    ax.set_yticks([5, 10, 20, 30, 50, 100, 150])
    ax.get_yaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax.grid(axis="y", color=GREY, linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.set_title("Video latency sampled from the live lab", pad=4)
    ax.set_xlabel("scenario (S1-S8 as in the scenario table)", fontsize=7.0)
    save(fig, out_dir, "fig_latency_phases")


# ---------------------------------------------------------------------------
# Figure 4: the per-sample distribution behind the violation decision
# ---------------------------------------------------------------------------

def figure_sample_distribution(measurements: list[dict[str, str]], out_dir: str) -> None:
    rows = [r for r in measurements if r.get("flow_key") == "h1->h4"]
    if not rows:
        return
    phases = [p for p in PHASE_ORDER if any(r.get("phase") == p for r in rows)]
    if not phases:
        return

    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    for i, phase in enumerate(phases):
        pooled: list[float] = []
        for r in rows:
            if r.get("phase") != phase:
                continue
            try:
                pooled.extend(float(s) for s in json.loads(r.get("samples") or "[]"))
            except (TypeError, ValueError):
                continue
        if not pooled:
            continue
        ax.scatter([i] * len(pooled), pooled, s=9, alpha=0.75, zorder=4,
                   color=BLUE if "congestion" in phase else CYAN,
                   edgecolor="white", linewidth=0.3)
        ax.plot([i - 0.3, i + 0.3], [statistics.fmean(pooled)] * 2, color=INK,
                linewidth=1.2, zorder=5)

    ax.axhline(50.0, color=RED, linewidth=1.0, linestyle=(0, (4, 2)))
    ax.text(len(phases) - 0.45, 50.0, " 50 ms", color=RED, fontsize=6.8,
            va="bottom", ha="right")
    ax.set_xticks(range(len(phases)))
    ax.set_xticklabels([PHASE_LABEL.get(p, p) for p in phases],
                       rotation=30, ha="right", fontsize=7.0)
    ax.set_ylabel("individual ping RTT (ms)")
    ax.set_yscale("log")
    ax.set_yticks([10, 20, 30, 50, 100, 150])
    ax.get_yaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax.grid(axis="y", color=GREY, linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.set_title("Every individual ping sample, not just the mean", pad=4)
    ax.set_xlabel("scenario (S1-S8 as in the scenario table)", fontsize=7.0)
    save(fig, out_dir, "fig_sample_distribution")


# ---------------------------------------------------------------------------
# Figure 5: the ranking ablation
# ---------------------------------------------------------------------------

def figure_ranking_ablation(ablations: list[dict[str, str]], out_dir: str) -> None:
    rows = [r for r in ablations if r.get("ablation") == "ranking_policy"]
    if not rows:
        print("    (skipping ranking ablation figure)")
        return

    fig, ax = plt.subplots(figsize=(3.4, 1.9))
    labels = []
    values = []
    colours = []
    for row in rows:
        policy = row["policy"]
        labels.append("system\n(congestion-aware)"
                      if policy.startswith("system")
                      else "ablation\n(shortest path only)")
        # The measured end-to-end latency of the path each policy selects.
        values.append(fnum(row.get("measured_latency_ms"), 0.0))
        colours.append(GREEN if row.get("crosses_congested_link") == "False"
                       else RED)
    if not any(values):
        return
    bars = ax.bar(range(len(values)), values, color=colours, width=0.5,
                  zorder=3)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value * 1.04,
                f"{value:.1f} ms", ha="center", fontsize=7.0, color=INK)
    ax.axhline(50.0, color=RED, linewidth=1.0, linestyle=(0, (4, 2)))
    ax.text(len(values) - 0.4, 50.0, " 50 ms", color=RED, fontsize=6.8,
            va="bottom", ha="right")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("measured latency (ms)")
    ax.grid(axis="y", color=GREY, linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.set_title("Path selected under congestion", pad=6, fontsize=8.0)
    save(fig, out_dir, "fig_ranking_ablation")


# ---------------------------------------------------------------------------
# Figure 6: the outcome matrix
# ---------------------------------------------------------------------------

def figure_outcomes(scenarios: list[dict[str, str]], out_dir: str) -> None:
    if not scenarios:
        return
    rows = list(scenarios)
    ORDER = ["baseline_healthy", "congestion_recovery", "congestion_partial",
             "healthy_impossible_target", "guardrail_blocked", "lab_offline",
             "offered_load_congestion", "degraded_no_oracle"]
    rows.sort(key=lambda r: (ORDER.index(r.get("scenario", ""))
                             if r.get("scenario", "") in ORDER else 99,
                             int(r.get("trial") or 0)))
    def outcome_of(row: dict[str, str]) -> str:
        return (row.get("outcome") or "").strip()

    short = {"baseline_healthy": "S1", "congestion_recovery": "S2",
             "congestion_partial": "S3",
             "healthy_impossible_target": "S4",
             "guardrail_blocked": "S5", "lab_offline": "S6",
             "offered_load_congestion": "S7",
             "degraded_no_oracle": "S8"}

    fig, ax = plt.subplots(figsize=(3.5, 2.2))
    ypos = list(range(len(rows)))[::-1]
    palette = {
        "intent_satisfied": GREEN,
        "partial_improvement": AMBER,
        "recovery_failed": RED,
        "unverified": MUTED,
        "abstained_insufficient_evidence": MUTED,
        "action_rejected": RED,
        "awaiting_approval": AMBER,
        "guardrail_blocked": MUTED,
        "lab_offline": MUTED,
        "no_better_path": BLUE,
    }
    for y, row in zip(ypos, rows):
        outcome = outcome_of(row)
        colour = palette.get(outcome, MUTED)
        ax.add_patch(plt.Circle((0.035, y), 0.030, color=colour, zorder=3))
        label = short.get(row.get("scenario", ""), "")
        # Every trial of a repeated scenario is labelled individually, so the
        # figure shows the distribution rather than five copies of one name.
        if row.get("trial"):
            label = f"{label}-{row['trial']}"
        ax.text(0.075, y, label,
                va="center", fontsize=5.8, color=MUTED)
        ax.text(0.165, y, outcome, va="center", fontsize=5.4, color=INK)
        before = row.get("before_latency_ms")
        after = row.get("after_latency_ms")
        if before not in (None, "", "None"):
            text = f"{fnum(before):.1f} $\\to$ {fnum(after):.1f} ms"
        else:
            text = "no change"
        ax.text(0.685, y, text, va="center", ha="center", fontsize=6.4,
                color=INK)
        path = row.get("executed_path") or ""
        if path:
            ax.text(0.995, y, path, va="center", ha="right", fontsize=6.0,
                    color=MUTED, family="monospace")
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.7, len(rows) - 0.3)
    ax.axis("off")
    ax.set_title("Outcome and measured effect of every scenario", pad=3,
                 fontsize=8.0)
    save(fig, out_dir, "fig_outcomes")


# ---------------------------------------------------------------------------
# Figure 7: link utilisation
# ---------------------------------------------------------------------------

def figure_utilisation(links: list[dict[str, str]], out_dir: str) -> None:
    rows = [r for r in links if r.get("scenario") in
            ("congestion_recovery", "offered_load_congestion")]
    if not rows:
        return
    link_ids = sorted({r["link_id"] for r in rows})
    scenarios = ["congestion_recovery", "offered_load_congestion"]

    fig, ax = plt.subplots(figsize=(3.4, 2.1))
    width = 0.34
    for si, scenario in enumerate(scenarios):
        values = []
        for lid in link_ids:
            vals = [fnum(r.get("utilization_pct")) for r in rows
                    if r.get("scenario") == scenario and r.get("link_id") == lid]
            values.append(max(vals) if vals else 0.0)
        xs = [i + (si - 0.5) * width for i in range(len(link_ids))]
        ax.bar(xs, values, width=width, zorder=3,
               color=BLUE if si == 0 else GREEN,
               label={"congestion_recovery": "delay fault (S2)",
                      "offered_load_congestion": "degraded + load (S7)"}[scenario])
    ax.axhline(80.0, color=RED, linewidth=1.0, linestyle=(0, (4, 2)))
    ax.text(-0.42, 80.0, "80% congestion threshold", color=RED,
            fontsize=6.0, va="bottom", ha="left")
    ax.set_xticks(range(len(link_ids)))
    ax.set_xticklabels(link_ids, rotation=20, ha="right")
    ax.set_ylabel("measured link utilisation (%)")
    ax.set_ylim(0, 100)
    ax.grid(axis="y", color=GREY, linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.legend(loc="upper left", fontsize=6.2)
    ax.set_title("Utilisation: delay fault moves no bytes, offered load does",
                 pad=4, fontsize=8.0)
    save(fig, out_dir, "fig_link_utilisation")


# ---------------------------------------------------------------------------
# Figure 8: fallback vs the model in the loop
# ---------------------------------------------------------------------------

def figure_llm(fallback: list[dict[str, str]], llm: list[dict[str, str]],
               out_dir: str) -> None:
    if not fallback or not llm:
        return
    scenarios = ["congestion_recovery", "congestion_partial",
                 "offered_load_congestion", "degraded_no_oracle"]
    labels = {"congestion_recovery": "S2", "congestion_partial": "S3",
              "offered_load_congestion": "S7", "degraded_no_oracle": "S8"}
    fig, axes = plt.subplots(1, 2, figsize=(6.9, 2.35))
    for ax, key, ylabel in (
            (axes[0], "after_latency_ms", "post-action latency (ms)"),
            (axes[1], "wall_clock_s", "wall-clock per run (s)")):
        for i, name in enumerate(scenarios):
            fb = [v for v in (fnum(r.get(key)) for r in fallback
                              if r.get("scenario") == name) if v is not None]
            lm = [v for v in (fnum(r.get(key)) for r in llm
                              if r.get("scenario") == name) if v is not None]
            ax.scatter([i - 0.16] * len(fb), fb, s=11, zorder=4,
                       color=BLUE, edgecolor="white", linewidth=0.5)
            ax.scatter([i + 0.16] * len(lm), lm, s=11, zorder=4,
                       color=GREEN, edgecolor="white", linewidth=0.5)
            if fb:
                ax.plot([i - 0.31, i - 0.01], [statistics.fmean(fb)] * 2,
                        color=INK, linewidth=1.2, zorder=3)
            if lm:
                ax.plot([i + 0.01, i + 0.31], [statistics.fmean(lm)] * 2,
                        color=INK, linewidth=1.2, zorder=3)
        ax.set_xticks(range(len(scenarios)))
        ax.set_xticklabels([labels[n] for n in scenarios])
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", color=GREY, linewidth=0.5, zorder=0)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
    axes[0].legend(handles=[
        plt.Line2D([0], [0], marker="o", color="w", label="fallback (no model)",
                   markerfacecolor=BLUE, markeredgecolor="white", markersize=6),
        plt.Line2D([0], [0], marker="o", color="w", label="model in the loop",
                   markerfacecolor=GREEN, markeredgecolor="white", markersize=6)],
        loc="upper left", fontsize=6.4, frameon=False)
    axes[0].set_ylim(24.9, 30.6)
    axes[0].set_title("Outcome quality is identical", pad=3, fontsize=8.0)
    axes[1].set_title("The model costs wall-clock", pad=3, fontsize=8.0)
    save(fig, out_dir, "fig_llm")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--figure-dir", required=True)
    ap.add_argument("--llm-data-dir", default=None)
    args = ap.parse_args(argv)

    data_dir = os.path.abspath(args.data_dir)
    out_dir = os.path.abspath(args.figure_dir)

    topology = load_json(os.path.join(data_dir, "topology.json"))
    measurements = load_csv(os.path.join(data_dir, "measurements.csv"))
    links = load_csv(os.path.join(data_dir, "link_utilisation.csv"))
    scenarios = load_csv(os.path.join(data_dir, "scenario_summary.csv"))
    ablations = load_csv(os.path.join(data_dir, "ablations.csv"))

    print(f"    reading {data_dir}")
    figure_topology(topology, out_dir)
    figure_workflow(out_dir)
    figure_latency_phases(measurements, out_dir)
    figure_sample_distribution(measurements, out_dir)
    figure_ranking_ablation(ablations, out_dir)
    figure_outcomes(scenarios, out_dir)
    figure_utilisation(links, out_dir)
    if args.llm_data_dir:
        llm_dir = os.path.abspath(args.llm_data_dir)
        llm_scenarios = load_csv(os.path.join(llm_dir, "scenario_summary.csv"))
        figure_llm(scenarios, llm_scenarios, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
