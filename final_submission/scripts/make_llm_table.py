#!/usr/bin/env python3
"""Generate tables_llm.tex + numbers_llm.tex from the LLM-active run's data.

  python final_submission/scripts/make_llm_table.py \
      --data-dir final_submission/data-llm \
      --fallback-dir final_submission/data-fallback \
      --out final_submission/paper/tables_llm.tex \
      --macro-out final_submission/paper/numbers_llm.tex

The deterministic-template diagnosis summary produced by the fallback is
recognised textually; anything else counts as model-written.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from typing import Any


def load_csv(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def fnum(value: Any):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v


def esc(text: str) -> str:
    """Minimal LaTeX escape for table cells (text outside math mode)."""
    repl = {"\\": "\\textbackslash{}", "&": "\\&", "%": "\\%",
            "#": "\\#", "_": "\\_", "{": "\\{", "}": "\\}"}
    return "".join(repl.get(c, c) for c in str(text))


def deterministic_template(summary: str) -> bool:
    # The fallback writes this exact sentence shape.
    return ("Suspected bottleneck link(s):" in summary
            and "is measured at" in summary)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--fallback-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--macro-out", required=True)
    args = ap.parse_args()

    llm_summary = load_csv(os.path.join(args.data_dir, "scenario_summary.csv"))
    fb_summary = load_csv(os.path.join(args.fallback_dir, "scenario_summary.csv"))
    llm_runs = json.load(open(os.path.join(args.data_dir, "runs.json")))
    fb_runs = json.load(open(os.path.join(args.fallback_dir, "runs.json")))

    fb_by_key = {r["scenario"] + (f"#{r['trial']}" if r.get("trial") else ""): r
                 for r in fb_summary}
    # align keys: harness writes f"{name}#trial{n}" for repeated runs
    fb_out = {}
    for key, rec in fb_runs.items():
        fb_out[key.split("#")[0]] = rec["record"].get("outcome")

    # What the operator's message asks for, per scenario (the fixed messages
    # run_experiments.py passes to the agent):
    expected_target = {
        "baseline_healthy": 50.0, "congestion_recovery": 50.0,
        "congestion_partial": 22.0, "healthy_impossible_target": 15.0,
        "guardrail_blocked": None, "lab_offline": 50.0,
        "offered_load_congestion": 50.0, "degraded_no_oracle": 50.0,
    }
    parse_ok = 0
    parse_total = 0
    narration_runs = 0
    diagnose_runs = 0
    fallback_narrations = 0
    parsed_by_model = 0
    parse_fallback = 0
    compare = 0
    for key, rec in llm_runs.items():
        scenario = key.split("#")[0]
        evs = rec.get("events", [])
        for e in evs:
            if e.get("type") == "intent_detected":
                parse_total += 1
                p = e.get("payload") or {}
                want = expected_target.get(scenario)
                ok = (p.get("src_host") == "h1" and p.get("dst_host") == "h4"
                      and float(p.get("max_latency_ms") or 0) == want)
                if ok:
                    parse_ok += 1
                # `parsed_by` says which parser produced the tuple: the
                # model, or the deterministic fallback after a provider
                # failure.
                if str(p.get("parsed_by", "")).startswith("intent parsed by"):
                    parsed_by_model += 1
                else:
                    parse_fallback += 1
                break
        summary = (rec["record"].get("diagnosis_summary") or "")
        if summary:
            diagnose_runs += 1
            if not deterministic_template(summary):
                narration_runs += 1
            else:
                fallback_narrations += 1
        fb = fb_out.get(key.split("#")[0])
        if fb == rec["record"].get("outcome"):
            compare += 1

    # Outcome comparison at the scenario level (trials grouped)
    llm_agg = load_csv(os.path.join(args.data_dir, "scenario_aggregates.csv"))
    order = ["baseline_healthy", "congestion_recovery", "congestion_partial",
             "degraded_no_oracle", "healthy_impossible_target", "guardrail_blocked",
             "offered_load_congestion", "lab_offline"]
    by = {r["scenario"]: r for r in llm_agg}
    rows = []
    for name in order:
        r = by.get(name)
        if not r:
            continue
        afters = [fnum(v) for v in (r.get("after_latency_min"), r.get("after_latency_max"))]
        lat = (f"{min(a for a in afters if a is not None):.1f}--"
               f"{max(a for a in afters if a is not None):.1f} ms"
               if all(a is not None for a in afters) else "--")
        rows.append((name, r["trials"], r["outcomes"], lat,
                     r.get("wall_clock_min_s", ""), r.get("wall_clock_max_s", "")))

    # The model that actually parsed the intents, read from the recorded
    # runs rather than hardcoded.
    model = ""
    for rec in llm_runs.values():
        for e in rec.get("events", []):
            if e.get("type") == "intent_detected":
                p = e.get("payload") or {}
                parsed_by = str(p.get("parsed_by", ""))
                if parsed_by.startswith("intent parsed by "):
                    model = parsed_by[len("intent parsed by "):].strip()
                break
        if model:
            break
    if not model:
        model = "the configured model"
    wc = [fnum(r.get("wall_clock_s")) for r in llm_summary]
    wc = [v for v in wc if v is not None]
    wall_mean = statistics.fmean(wc) if wc else 0.0
    wall_max = max(wc) if wc else 0.0

    lines = []
    lines.append("% GENERATED by make_llm_table.py -- do not edit by hand")
    lines.append(r"\begin{table}[t]")
    lines.append(r"\centering")
    lines.append(r"\tiny")
    lines.append(r"\caption{The same protocol run with the model active"
                 r" (\texttt{" + model + r"}, Groq, intent parser and narrative; the"
                 r" deterministic gates, planner and guardrail are unchanged)."
                 r" Outcome distribution matches the fallback run in Section VIII-A;"
                 r" wall-clock is measured from first observation to verified outcome."
                 r" From \texttt{final\_submission/data-llm/}.}")
    lines.append(r"\label{tab:llm}")
    lines.append(r"\begin{tabular}{@{}l r l l r@{}}")
    lines.append(r"\toprule")
    lines.append(r"Scenario & Tri. & Outcome(s) & After (min--max) & Wall (min--max) \\")
    lines.append(r"\midrule")
    for name, trials, outcomes, lat, wlo, whi in rows:
        esc_name = esc(name.replace("_", " "))
        wall = ""
        if wlo and whi:
            wall = f"{float(wlo):.1f}--{float(whi):.1f} s"
        lines.append(f"{esc_name} & {trials} & \\texttt{{{esc(outcomes)}}} & "
                     f"{lat} & {wall} \\\\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    lines.append("")
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    macro_lines = [
        "% Numbers for the LLM-active replication (VIII-E).",
        "\\newcommand{\\LlmModel}{" + model + "}",
        "\\newcommand{\\LlmParseOk}{%d}" % parse_ok,
        "\\newcommand{\\LlmNarrationRuns}{%d}" % narration_runs,
        "\\newcommand{\\LlmDiagnoseRuns}{%d}" % diagnose_runs,
        "\\newcommand{\\LlmParsedByModel}{%d}" % parsed_by_model,
        "\\newcommand{\\LlmParseFallback}{%d}" % parse_fallback,
        "\\newcommand{\\LlmFallbackNarrations}{%d}" % fallback_narrations,
        "\\newcommand{\\LlmRuns}{%d}" % len(llm_runs),
        "\\newcommand{\\LlmOutcomesMatch}{%d}" % compare,
        "\\newcommand{\\LlmClockMean}{%.1f}" % wall_mean,
        "\\newcommand{\\LlmClockMax}{%.1f}" % wall_max,
    ]
    with open(args.macro_out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(macro_lines) + "\n")
    print("wrote", args.out, "and", args.macro_out)
    print("parse_ok", parse_ok, "of", len(llm_runs),
          "| model-narration runs:", narration_runs,
          "| outcomes matching fallback:", compare, "of", len(llm_runs),
          "| wall mean %.1fs max %.1fs" % (wall_mean, wall_max))
    return 0


if __name__ == "__main__":
    sys.exit(main())
