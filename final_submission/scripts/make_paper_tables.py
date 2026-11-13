#!/usr/bin/env python3
"""Generate the paper's LaTeX tables from the recorded experiment data.

Every number that appears in a table of the paper comes from this script, so
a table can never drift away from the data that produced it. The script
reads only `final_submission/data/` and writes
`final_submission/paper/tables.tex`, which `paper.tex` includes.

Usage:
    make_paper_tables.py --data-dir final_submission/data \
                          --out final_submission/paper/tables.tex
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
from typing import Any

SCENARIO_ORDER = [
    "baseline_healthy",
    "congestion_recovery",
    "congestion_partial",
    "healthy_impossible_target",
    "guardrail_blocked",
    "lab_offline",
    "offered_load_congestion",
    "degraded_no_oracle",
]

SCENARIO_SHORT = {
    "baseline_healthy": "S1 healthy",
    "congestion_recovery": "S2 degraded",
    "congestion_partial": "S3 degraded",
    "healthy_impossible_target": "S4 healthy",
    "guardrail_blocked": "S5 guardrail",
    "lab_offline": "S6 lab offline",
    "offered_load_congestion": "S7 load",
    "degraded_no_oracle": "S8 degraded, no flag",
    "after_restart": "S1' restart",
}

SCENARIO_TEXT = {
    "baseline_healthy": "Healthy network, 50~ms intent",
    "congestion_recovery": "Bottleneck degraded, 50~ms intent (5 trials)",
    "congestion_partial": "Bottleneck degraded, 22~ms intent",
    "degraded_no_oracle": "Degraded, fault invisible to the snapshot",
    "healthy_impossible_target": "Healthy network, 15~ms intent",
    "guardrail_blocked": "Request to disable a gate",
    "lab_offline": "Network lab stopped",
    "offered_load_congestion": "Background UDP + degraded bottleneck, 50~ms intent",
}


def load_csv(path: str) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _fmt_outcomes(outcomes: str) -> str:
    """`intent_satisfied=3` -> `intent_satisfied (3)`."""
    parts = []
    for item in (outcomes or "").split(";"):
        if not item:
            continue
        name, _, count = item.partition("=")
        parts.append(f"{name} ({count})" if count else name)
    return ", ".join(parts)


def _mean_of(values: list[Any]) -> float:
    vals = [v for v in values if v is not None]
    return statistics.fmean(vals) if vals else 0.0


def fnum(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def esc(text: str) -> str:
    return (text or "").replace("_", r"\_").replace("&", r"\&").replace("%", r"\%")


def sample_peak(measurements: list[dict[str, str]], phase: str,
                flow: str = "h1->h4") -> float | None:
        """The largest individual ping sample of a phase.

        Table II's `Peak` column is this number; the prose in Section VIII-A
        quotes the largest *snapshot mean* separately, and the two are
        different quantities.
        """
        vals: list[float] = []
        for r in measurements:
            if r["flow_key"] != flow or r["phase"] != phase:
                continue
            try:
                vals.extend(float(x) for x in json.loads(r.get("samples") or "[]"))
            except (TypeError, ValueError):
                continue
        return max(vals) if vals else None

def samples_for(measurements: list[dict[str, str]], phase: str,
                mean_value: float, flow: str = "h1->h4") -> str:
    """The 10-sample vector of the snapshot whose sample-mean matches.

    Matches on the sample mean computed from the recorded vector rather
    than on the reported ``latency_ms`` (same physical quantity, recorded
    at different precision; a reported latency need not be carried by any
    single snapshot).
    """
    best: list[str] = []
    best_diff = float("inf")
    for r in measurements:
        if r["flow_key"] != flow or r["phase"] != phase:
            continue
        try:
            samples = [float(x) for x in json.loads(r.get("samples") or "[]")]
        except (TypeError, ValueError):
            continue
        if not samples:
            continue
        diff = abs(statistics.fmean(samples) - mean_value)
        if diff < best_diff:
            best_diff = diff
            best = samples
    return ", ".join("%.1f" % x for x in best)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    data_dir = os.path.abspath(args.data_dir)
    measurements = load_csv(os.path.join(data_dir, "measurements.csv"))
    summary_rows = load_csv(os.path.join(data_dir, "scenario_summary.csv"))
    scenarios = load_csv(os.path.join(data_dir, "scenario_summary.csv"))
    ablations = load_csv(os.path.join(data_dir, "ablations.csv"))

    by_scenario = {r["scenario"]: r for r in scenarios}

    # -- per-phase statistics -------------------------------------------
    phases: list[str] = []
    for r in measurements:
        if r["flow_key"] == "h1->h4" and r["phase"] not in phases:
            phases.append(r["phase"])
    all_phases = list(phases)
    phases = [p for p in SCENARIO_ORDER if p in phases]
    phases += [p for p in all_phases if p not in phases]

    lines: list[str] = []
    lines.append("% GENERATED BY final_submission/scripts/make_paper_tables.py")
    lines.append("% Do not edit by hand: regenerate with")
    lines.append("%   python final_submission/scripts/make_paper_tables.py \\")
    lines.append("%       --data-dir final_submission/data \\")
    lines.append("%       --out final_submission/paper/tables.tex")
    lines.append("")

    # ---- Table: measured latency per phase ----------------------------
    lines.append(r"\begin{table*}[t]")
    lines.append(r"\centering")
    lines.append(r"\scriptsize")
    lines.append(r"\caption{Measured video-flow latency ($h1\!\to\!h4$) in every "
                 r"phase of the experiment (S6, with the lab stopped, "
                 r"yields no samples and is omitted), and the protected "
                 r"voice flow "
                 r"($h3\!\to\!h4$) measured in the same snapshots. Each "
                 r"phase is five independent snapshots of ten ICMP samples "
                 r"each; \emph{Snaps.} is the number of snapshots, not the "
                 r"per-snapshot sample count ($10$). \emph{Mean} is the mean "
                 r"of the per-snapshot means; \emph{Peak} is the largest "
                 r"single ping sample seen in the phase, not the largest "
                 r"snapshot mean. The agent's own before/after measurements "
                 r"(Tab.~\ref{tab:outcomes}) are taken independently of the "
                 r"phase sampling and can differ from it. From "
                 r"\texttt{final\_submission/data/measurements.csv}.}")
    lines.append(r"\label{tab:phases}")
    lines.append(r"\begin{tabular}{llrrrrrr}")
    lines.append(r"\toprule")
    lines.append(r"Phase & Intent & Snaps. & Mean & Median & Std.~dev. & Peak & Voice \\")
    lines.append(r"\midrule")
    for phase in phases:
        values = [fnum(r["latency_ms"]) for r in measurements
                  if r["flow_key"] == "h1->h4" and r["phase"] == phase]
        values = [v for v in values if v is not None]
        if not values:
            continue
        voice = [fnum(r["latency_ms"]) for r in measurements
                 if r["flow_key"] == "h3->h4" and r["phase"] == phase]
        voice = [v for v in voice if v is not None]
        target = {"baseline_healthy": "50", "congestion_recovery": "50",
                  "congestion_partial": "22",
                  "healthy_impossible_target": "15",
                  "guardrail_blocked": "--", "lab_offline": "50",
                  "offered_load_congestion": "50",
                 "degraded_no_oracle": "50"}.get(phase, "--")
        target = target if target == "--" else target + "~ms"
        voice_txt = f"{statistics.fmean(voice):.1f}" if voice else "--"
        samples_peak = [fnum(r["max_latency_ms"]) for r in measurements
                        if r["flow_key"] == "h1->h4" and r["phase"] == phase]
        samples_peak = [v for v in samples_peak if v is not None]
        peak = max(samples_peak) if samples_peak else max(values)
        short = {"baseline_healthy": "S1 healthy",
                 "congestion_recovery": "S2 degraded",
                 "congestion_partial": "S3 degraded",
                 "healthy_impossible_target": "S4 healthy",
                 "guardrail_blocked": "S5 healthy",
                 "lab_offline": "S6 lab down",
                 "offered_load_congestion": "S7 degraded+load",
                 "degraded_no_oracle": "S8 degraded, no flag",
                 "after_restart": "S1' restart"}.get(phase,
                                                  phase.replace("_", " "))
        lines.append(
            f"{esc(short)} & {target} & {len(values)} & "
            f"{statistics.fmean(values):.1f} & {statistics.median(values):.1f} & "
            f"{(statistics.pstdev(values) if len(values) > 1 else 0.0):.1f} & "
            f"{peak:.1f} & {voice_txt} \\\\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table*}")
    lines.append("")

    # ---- Table: scenario outcomes --------------------------------------
    lines.append(r"\begin{table*}[t]")
    lines.append(r"\centering")
    lines.append(r"\scriptsize")
    lines.append(r"\caption{Outcome of every agent run. ``Before'' and "
                 r"``after'' are the latencies the verification gate measured; "
                 r"``--'' means the run never changed the network. The gate "
                 r"column is the number of the nine deterministic safety "
                 r"checks that passed. From "
                 r"\texttt{final\_submission/data/scenario\_summary.csv}.}")
    lines.append(r"\label{tab:outcomes}")
    lines.append(r"\begin{tabular}{llrrrl}")
    lines.append(r"\toprule")
    lines.append(r"Scenario & Outcome & Before & After & Installed path & Gates \\")
    lines.append(r"\midrule")
    for name in SCENARIO_ORDER:
        rows = [r for r in scenarios if r["scenario"] == name]
        if not rows:
            continue
        if len(rows) > 1:
            # Repeated trial: report the distribution, not one trial.
            bs = [fnum(r.get("before_latency_ms")) for r in rows]
            as_ = [fnum(r.get("after_latency_ms")) for r in rows]
            bs = [v for v in bs if v is not None]
            as_ = [v for v in as_ if v is not None]
            outcomes = {r.get("outcome", "") for r in rows}
            if len(outcomes) == 1:
                outcome_txt = f"{outcomes.pop()} ({len(rows)} trials)"
            else:
                outcome_txt = "; ".join(
                    f"{k} x{v}" for k, v in sorted(
                        ((o, sum(1 for r in rows if r.get("outcome") == o))
                         for o in outcomes), key=lambda kv: kv[0]))
            after_txt = (f"{statistics.fmean(as_):.1f} "
                         f"[{min(as_):.1f}--{max(as_):.1f}]") if as_ else "--"
            before_txt = (f"{statistics.fmean(bs):.1f} "
                          f"[{min(bs):.1f}--{max(bs):.1f}]") if bs else "--"
            paths = "; ".join(sorted({r.get("executed_path") or ""
                                     for r in rows} - {""}))
            path_txt = paths if paths else "--"
        else:
            row = rows[0]
            before = fnum(row.get("before_latency_ms"))
            after = fnum(row.get("after_latency_ms"))
            before_txt = ("%.1f" % before) if before is not None else "--"
            after_txt = ("%.1f" % after) if after is not None else "--"
            outcome_txt = row.get("outcome", "")
            path_txt = row.get("executed_path") or "--"
        gates = rows[-1].get("validation_checks_passed", "0")
        total = rows[-1].get("validation_checks_total", "0")
        gates_txt = f"{gates}/{total}" if total and total != "0" else "--"
        lines.append(
            f"{esc(SCENARIO_SHORT.get(name, name))} & "
            f"\\texttt{{{esc(outcome_txt)}}} & "
            f"{esc(before_txt)} & {esc(after_txt)} & "
            f"\\texttt{{{esc(path_txt)}}} & {gates_txt} \\\\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table*}")
    lines.append("")

    # ---- Table: per-scenario aggregates --------------------------------
    agg_rows = load_csv(os.path.join(data_dir, "scenario_aggregates.csv"))
    if agg_rows:
        lines.append(r"\begin{table*}[t]")
        lines.append(r"\scriptsize")
        lines.append(r"\centering")
        lines.append(r"\caption{Outcome distribution per scenario. The "
                     r"active-rerouting scenarios (S2 recovery, S3 partial "
                     r"improvement, S7 offered load, S8 no-oracle) are "
                     r"repeated as five independent trials, each from a "
                     r"freshly built lab; the other scenarios are run "
                     r"once. The latency column is the post-action latency "
                     r"the verification gate measured; the wall-clock "
                     r"column is the end-to-end closed-loop duration "
                     r"(pre-action snapshot to verified outcome), min--max "
                     r"over the trials. From "
                     r"\texttt{final\_submission/data/scenario\_aggregates.csv}.}")
        lines.append(r"\label{tab:aggregates}")
        lines.append(r"\begin{tabular}{llrrll}")
        lines.append(r"\toprule")
        lines.append(r"Scenario & Trials & Outcome(s) & After (min--max) & "
                     r"Wall-clock (min--max) & Path \\")
        lines.append(r"\midrule")
        for row in agg_rows:
            lo = row.get("after_latency_min", "")
            hi = row.get("after_latency_max", "")
            lat = (f"{float(lo):.1f}--{float(hi):.1f} ms" if lo and hi
                   else "--")
            wlo = row.get("wall_clock_min_s", "")
            whi = row.get("wall_clock_max_s", "")
            wall = (f"{float(wlo):.1f}--{float(whi):.1f} s" if wlo and whi
                    else "--")
            lines.append(
                f"{esc(SCENARIO_SHORT.get(row['scenario'], row['scenario'].replace('_', ' ')))} & "
                f"{row['trials']} & "
                f"\\texttt{{{esc(_fmt_outcomes(row['outcomes']))}}} & "
                f"{lat} & {wall} & \\texttt{{{esc(row['paths'] or '--')}}} \\\\")
        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{table*}")
        lines.append("")

    # ---- Table: the ranking ablation -----------------------------------
    ranking = [r for r in ablations if r["ablation"] == "ranking_policy"]
    if ranking:
        lines.append(r"\begin{table}[t]")
        lines.append(r"\centering")
        lines.append(r"\caption{Path selection under the degraded bottleneck. "
                     r"Both rows rank the \emph{same} candidate set the lab "
                     r"produced, so the comparison isolates the ranking rule. "
                     r"The measured latency is what the live network recorded "
                     r"on the selected path. From "
                     r"\texttt{final\_submission/data/ablations.csv}.}")
        lines.append(r"\label{tab:ranking}")
        lines.append(r"\begin{tabular}{llr}")
        lines.append(r"\toprule")
        lines.append(r"Ranking rule & Path & Latency \\")
        lines.append(r"\midrule")
        labels = {"system_congestion_aware": "system (congestion-aware)",
                  "ablation_shortest_path_only": "ablation (shortest path)"}
        for row in ranking:
            measured = fnum(row.get("measured_latency_ms"))
            path = "-".join(p for p in (row.get("chosen_path") or "").split("-")
                            if not p.startswith("h"))
            lines.append(
                f"{esc(labels.get(row['policy'], row['policy']))} & "
                f"\\texttt{{{esc(path)}}} & "
                f"{('%.1f' % measured) if measured is not None else '--'}~ms \\\\")
        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{table}")
        lines.append("")

    PHASE_SHORT = {
        "baseline_healthy": "S1 healthy",
        "congestion_recovery": "S2 degraded",
        "congestion_partial": "S3 degraded",
        "healthy_impossible_target": "S4 healthy",
        "guardrail_blocked": "S5 guardrail",
        "lab_offline": "S6 offline",
        "offered_load_congestion": "S7 degraded+load",
        "degraded_no_oracle": "S8 no flag",
    }

    # ---- Table: the violation-rule ablation ----------------------------
    disagreements = [r for r in ablations
                     if r["ablation"] == "violation_rule"
                     and r["policy"] == "system_sample_distribution"]
    if disagreements:
        # Keep the hardest call in each direction: for each verdict
        # direction, keep the row whose target sits closest to the observed
        # mean (smallest |mean - target|), breaking ties by target.
        best_by_direction: dict[str, tuple] = {}
        for row in disagreements:
            detail = row.get("detail", "")
            try:
                target = float(detail.split("target ")[1].split(" ms")[0])
                mean = float(detail.split("(mean ")[1].split(" ms")[0])
            except (IndexError, ValueError):
                continue
            distribution = None
            m = re.search(r"distribution rule says violated=(\w+)", detail)
            if m:
                distribution = m.group(1)
            verdict = ("no / yes" if distribution == "False" else "yes / no")
            phase = row.get("scenario", "").split(":")[0]
            key = (abs(mean - target), target)
            if verdict not in best_by_direction or key < best_by_direction[verdict][0]:
                best_by_direction[verdict] = (key, target, mean, phase)
        lines.append(r"\begin{table}[t]")
        lines.append(r"\centering")
        lines.append(r"\caption{Recorded measurements on which the two "
                     r"violation rules disagree, one row per direction. The "
                     r"system treats a flow as violated when at least 25\% "
                     r"of its individual ping samples exceed the target; "
                     r"the mean-only ablation compares only the mean. "
                     r"\emph{System / mean-only} reports each rule's "
                     r"verdict on the intent (yes = violated). \texttt{yes / "
                     r"no} is the oscillation case the sample-distribution "
                     r"rule is built to catch; \texttt{no / yes} is the "
                     r"outlier-tolerance case (one high sample, below "
                     r"$\gamma$). Rows are the closest calls in the "
                     r"recorded data (smallest $|\bar{s}-\tau|$), from a "
                     r"millisecond sweep of $\tau$ from $5$ to $200$~ms, "
                     r"not an estimate of the deployed operating point. From "
                     r"\texttt{final\_submission/data/ablations.csv}.}")
        lines.append(r"\label{tab:violation}")
        lines.append(r"\tiny")
        lines.append(r"\begin{tabular}{@{}l r r l >{\raggedright\arraybackslash}p{0.34\columnwidth}@{}}")
        lines.append(r"\toprule")
        lines.append(r"Phase & Target & Mean & Sys / mean & Samples (ms) \\")
        lines.append(r"\midrule")
        for verdict in ("no / yes", "yes / no"):
            entry = best_by_direction.get(verdict)
            if entry is None:
                continue
            _key, target, mean, phase = entry
            samples = samples_for(measurements, phase, mean)
            lines.append(f"{esc(PHASE_SHORT.get(phase, phase.replace('_', ' ')))} & "
                         f"{target:g} & {mean:.1f} & {verdict} & "
                         f"{{\\ttfamily\\scriptsize {samples}}} \\\\")
        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{table}")
        lines.append("")

    # ---- Table: the always-act ablation --------------------------------
    always = [r for r in ablations
              if r["ablation"] == "always_act_vs_abstain"]
    if always:
        lines.append(r"\begin{table}[t]")
        lines.append(r"\centering")
        lines.append(r"\caption{Acting when there is nothing to act on. The "
                     r"system ends a healthy run with no flow rules "
                     r"installed; the always-act variant takes the same code "
                     r"path the agent uses when a target is unreachable, so "
                     r"its cost is measured rather than asserted. From "
                     r"\texttt{final\_submission/data/ablations.csv}.}")
        lines.append(r"\label{tab:alwaysact}")
        lines.append(r"\scriptsize")
        lines.append(r"\begin{tabular}{llr}")
        lines.append(r"\toprule")
        lines.append(r"Policy & Outcome & Healthy flow \\")
        lines.append(r"\midrule")
        for row in always:
            policy = ("system" if row["policy"].startswith("system")
                      else "always act")
            latency = fnum(row.get("measured_latency_ms"))
            if policy == "system":
                effect = f"{latency:.1f}~ms, unchanged"
                outcome = "no-op (nothing installed)"
            else:
                baseline = next(
                    (fnum(r.get("measured_latency_ms")) for r in always
                     if r["policy"].startswith("system")), None)
                outcome = "rerouted"
                pct = (100.0 * (latency - baseline) / baseline
                       if baseline and latency is not None else 0.0)
                effect = (f"{baseline:.1f}~$\\to$~{latency:.1f}~ms "
                          f"({pct:+.0f}\\%)"
                          if baseline is not None and latency is not None
                          else "rerouted")
            lines.append(
                f"{esc(policy)} & \\texttt{{{esc(outcome)}}} & {effect} \\\\")
        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{table}")
        lines.append("")

    # ---- Macros for the numbers quoted in the prose --------------------
    def stats(phase: str, flow: str = "h1->h4") -> list[float]:
        vals = [fnum(r["latency_ms"]) for r in measurements
                if r["flow_key"] == flow and r["phase"] == phase]
        return [v for v in vals if v is not None]

    def mean(phase: str, flow: str = "h1->h4") -> float | None:
        vals = stats(phase, flow)
        return statistics.fmean(vals) if vals else None


    def after(name: str) -> float | None:
        row = by_scenario.get(name) or {}
        return fnum(row.get("after_latency_ms"))

    def before(name: str) -> float | None:
        row = by_scenario.get(name) or {}
        return fnum(row.get("before_latency_ms"))

    utils = [fnum(r["utilization_pct"]) or 0.0 for r in
             load_csv(os.path.join(data_dir, "link_utilisation.csv"))]
    always_r = next((r for r in ablations
                     if r["ablation"] == "always_act_vs_abstain"
                     and not r["policy"].startswith("system")), {})
    always_l = fnum(always_r.get("measured_latency_ms"))
    always_base = fnum(next((r for r in ablations
                             if r["ablation"] == "always_act_vs_abstain"
                             and r["policy"].startswith("system")), {}).get(
                             "measured_latency_ms"))
    always_pct = (100.0 * (always_l - always_base) / always_base
                  if always_l is not None and always_base else None)
    agg = {r["scenario"]: r for r in
           load_csv(os.path.join(data_dir, "scenario_aggregates.csv"))}
    recovery_agg = agg.get("congestion_recovery", {})
    partial_agg = agg.get("congestion_partial", {})
    offered_agg = agg.get("offered_load_congestion", {})
    nooracle_agg = agg.get("degraded_no_oracle", {})
    voice_all = [v for phase in phases
                 for v in stats(phase, "h3->h4")]

    macros = [
        ("BaselineMean", "%.1f" % (mean("baseline_healthy") or 0.0)),
        ("BaselineStd", "%.1f" % ((statistics.pstdev(stats("baseline_healthy"))
                                   if len(stats("baseline_healthy")) > 1 else 0.0))),
        ("BaselinePeak", "%.1f" % (max(stats("baseline_healthy")) if
                                   stats("baseline_healthy") else 0.0)),
        ("CongestedMean", "%.1f" % (mean("congestion_recovery") or 0.0)),
        ("CongestedPeak", "%.1f" % (max(stats("congestion_recovery")) if
                                    stats("congestion_recovery") else 0.0)),
        # The recovery scenario is run as several trials, so the figure the
        # prose quotes is the mean over the trials, not one run.
        ("RecoveryAfter", "%.1f" % fnum(recovery_agg.get("after_latency_mean"),
                                         after("congestion_recovery") or 0.0)),
        ("RecoveryBefore", "%.1f" % (before("congestion_recovery") or 0.0)),
        ("PartialAfter", "%.1f" % (after("congestion_partial") or 0.0)),
        ("PartialBefore", "%.1f" % (before("congestion_partial") or 0.0)),
        ("FailedBefore", "%.1f" % (before("healthy_impossible_target") or 0.0)),
        ("FailedAfter", "%.1f" % (after("healthy_impossible_target") or 0.0)),
        ("VoiceMean", "%.1f" % (statistics.fmean(voice_all) if voice_all else 0.0)),
        ("VoiceMin", "%.1f" % (min(voice_all) if voice_all else 0.0)),
        ("VoiceMax", "%.1f" % (max(voice_all) if voice_all else 0.0)),
        ("UtilMax", "%.3f" % (max(utils) if utils else 0.0)),
        ("AltPathLatency", "%.1f" % (after("healthy_impossible_target") or 0.0)),
        ("RecoveryTrials", recovery_agg.get("trials", "5")),
        ("RecoveryAfterMin", "%.1f" % fnum(recovery_agg.get("after_latency_min"), 0.0)),
        ("RecoveryAfterMean", "%.1f" % fnum(recovery_agg.get("after_latency_mean"), 0.0)),
        ("RecoveryAfterMax", "%.1f" % fnum(recovery_agg.get("after_latency_max"), 0.0)),
        # Wall-clock of the whole closed-loop run (pre-action snapshot to
        # post-action measurement), per active-recovery scenario.
        ("RecoveryClockMin", "%.2f" % fnum(recovery_agg.get("wall_clock_min_s"), 0.0)),
        ("RecoveryClockMean", "%.2f" % fnum(recovery_agg.get("wall_clock_mean_s"), 0.0)),
        ("RecoveryClockMax", "%.2f" % fnum(recovery_agg.get("wall_clock_max_s"), 0.0)),
        ("PartialTrials", partial_agg.get("trials", "1")),
        ("PartialAfterMin", "%.1f" % fnum(partial_agg.get("after_latency_min"), 0.0)),
        ("PartialAfterMean", "%.1f" % fnum(partial_agg.get("after_latency_mean"), 0.0)),
        ("PartialAfterMax", "%.1f" % fnum(partial_agg.get("after_latency_max"), 0.0)),
        ("PartialClockMean", "%.2f" % fnum(partial_agg.get("wall_clock_mean_s"), 0.0)),
        ("OfferedTrials", offered_agg.get("trials", "1")),
        ("OfferedAfterMin", "%.1f" % fnum(offered_agg.get("after_latency_min"), 0.0)),
        ("OfferedAfterMean", "%.1f" % fnum(offered_agg.get("after_latency_mean"), 0.0)),
        ("OfferedAfterMax", "%.1f" % fnum(offered_agg.get("after_latency_max"), 0.0)),
        ("OfferedClockMean", "%.2f" % fnum(offered_agg.get("wall_clock_mean_s"), 0.0)),
        ("NoOracleTrials", nooracle_agg.get("trials", "1")),
        ("NoOracleAfterMin", "%.1f" % fnum(nooracle_agg.get("after_latency_min"), 0.0)),
        ("NoOracleAfterMean", "%.1f" % fnum(nooracle_agg.get("after_latency_mean"), 0.0)),
        ("NoOracleAfterMax", "%.1f" % fnum(nooracle_agg.get("after_latency_max"), 0.0)),
        ("NoOracleClockMean", "%.2f" % fnum(nooracle_agg.get("wall_clock_mean_s"), 0.0)),
        ("BaselineSamplePeak", "%.1f" % (sample_peak(measurements, "baseline_healthy") or 0.0)),
        ("CongestedSamplePeak", "%.1f" % (sample_peak(measurements, "congestion_recovery") or 0.0)),
        ("SevPhaseMean", "%.1f" % (mean("offered_load_congestion") or 0.0)),
        ("SevBeforeMean", "%.1f" % _mean_of(
            [fnum(r.get("before_latency_ms")) for r in summary_rows
             if r.get("scenario") == "offered_load_congestion"])),
        ("AlwaysActLatency", "%.1f" % (always_l if always_l is not None else 0.0)),
        ("AlwaysActBase", "%.1f" % (always_base if always_base else 0.0)),
        ("AlwaysActPct", "+%.0f" % (always_pct if always_pct else 0.0)),
    ]
    lines.append("% Numbers quoted in the prose, computed from the same data.")
    for name, value in macros:
        lines.append("\\newcommand{\\%s}{%s}" % (name, value))
    lines.append("")

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)

    # The macro definitions go into a separate file: they are needed in the
    # preamble (the abstract quotes them), and a float environment cannot
    # be placed there.
    macro_path = os.path.join(out_dir, "numbers.tex")
    table_path = args.out
    macro_lines = [ln for ln in lines if ln.startswith("\\newcommand")
                   or ln.startswith("% Numbers quoted")]
    table_lines = [ln for ln in lines if not (ln.startswith("\\newcommand")
                   or ln.startswith("% Numbers quoted"))]

    with open(macro_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(macro_lines) + "\n")
    with open(table_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(table_lines))
    print(f"    wrote {macro_path} and {table_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
