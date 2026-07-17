#!/usr/bin/env python3
"""Aggregate a bench/run_sweep.py results JSONL into per-condition stats.

Each line of the input file is a record written by run_sweep.py:
    {"label": ..., "trial": ..., "timestamp": ..., "args": {...}, "result": {...}}

where "result" is exactly what synthetic_multiturn_test.py's run_streaming/
run_nonstreaming return. This module recomputes the same per-trial metrics
that synthetic_multiturn_test.py's own summarize() prints (total tok/s,
thinking-token fraction, visible-phase tok/s for streaming trials) as a pure
function returning numbers rather than a formatted string, then groups by
label and reports median + IQR per group.

Usage:
    uv run python bench/analyze_results.py bench/results/sweep.jsonl
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict


def compute_metrics(result):
    """Mirror synthetic_multiturn_test.py's summarize() math, returning raw
    numbers instead of a formatted string. Any trial missing output_tokens
    (a failed/malformed record) yields all-None metrics rather than raising,
    so one bad trial doesn't crash the whole aggregation -- it's excluded
    from that metric's stats instead (see aggregate_group).
    """
    out = result.get("output_tokens") or 0
    wall_ms = result.get("wall_ms")
    wall_s = wall_ms / 1000 if wall_ms else None

    thinking_chars = result.get("thinking_chars", 0)
    text_chars = result.get("text_chars", 0)
    total_chars = thinking_chars + text_chars or 1

    usage = result.get("usage") or {}
    details = usage.get("output_tokens_details") or {}
    thinking_tokens_exact = details.get("thinking_tokens")
    if thinking_tokens_exact is not None and out:
        visible_tokens_est = out - thinking_tokens_exact
        thinking_frac = thinking_tokens_exact / out
    elif out:
        visible_frac = text_chars / total_chars
        visible_tokens_est = out * visible_frac
        thinking_frac = 1 - visible_frac
    else:
        visible_tokens_est = None
        thinking_frac = None

    total_tps = (out / wall_s) if (out and wall_s) else None

    visible_tps_phase = None
    ttft_text_ms = result.get("ttft_text_ms")
    if ttft_text_ms and wall_s and visible_tokens_est is not None:
        pre_text_s = ttft_text_ms / 1000
        post_text_s = wall_s - pre_text_s
        if post_text_s > 0:
            visible_tps_phase = visible_tokens_est / post_text_s

    return {
        "wall_s": wall_s,
        "output_tokens": out or None,
        "thinking_frac": thinking_frac,
        "total_tps": total_tps,
        "visible_tps_phase": visible_tps_phase,
    }


def median_iqr(values):
    """Return (median, low, high, n, is_iqr). For n>=4 low/high are real
    quartiles (Q1/Q3). Below that a quartile split is meaningless -- rather
    than collapsing to a fake zero-width "IQR" (which silently hides real
    trial-to-trial spread, e.g. for the n=2-3 trial counts this project's
    cost-conscious sweeps default to), low/high fall back to observed
    min/max and is_iqr=False so callers can label it "range" instead."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None, None, None, 0, True
    n = len(vals)
    med = statistics.median(vals)
    if n >= 4:
        q1 = statistics.median(vals[: n // 2])
        q3 = statistics.median(vals[(n + 1) // 2 :])
        is_iqr = True
    else:
        q1, q3 = vals[0], vals[-1]
        is_iqr = False
    return med, q1, q3, n, is_iqr


def fmt(med, q1, q3, unit="", is_iqr=True):
    if med is None:
        return "n/a"
    label = "IQR" if is_iqr else "range"
    return f"{med:.2f}{unit} ({label} {q1:.2f}-{q3:.2f}{unit})"


def load_records(path):
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def group_by_label(records):
    groups = defaultdict(list)
    for rec in records:
        groups[rec["label"]].append(rec)
    return groups


def report(groups):
    lines = []
    for label in sorted(groups):
        trials = groups[label]
        metrics = [compute_metrics(t["result"]) for t in trials]
        n = len(trials)
        wall_med, wall_q1, wall_q3, wall_n, wall_iqr = median_iqr(
            m["wall_s"] for m in metrics
        )
        tps_med, tps_q1, tps_q3, tps_n, tps_iqr = median_iqr(
            m["total_tps"] for m in metrics
        )
        think_med, think_q1, think_q3, think_n, think_iqr = median_iqr(
            m["thinking_frac"] for m in metrics
        )
        vis_med, vis_q1, vis_q3, vis_n, vis_iqr = median_iqr(
            m["visible_tps_phase"] for m in metrics
        )
        truncated = sum(
            1 for t in trials if t["result"].get("stop_reason") == "max_tokens"
        )
        lines.append(f"=== {label} (n={n}) ===")
        lines.append(
            f"  wall_s:              {fmt(wall_med, wall_q1, wall_q3, 's', wall_iqr)}"
        )
        lines.append(
            f"  total_tok_s:         {fmt(tps_med, tps_q1, tps_q3, ' tok/s', tps_iqr)}"
        )
        lines.append(
            f"  thinking_frac:       {fmt(think_med, think_q1, think_q3, '', think_iqr)}"
            + (f"  [n={think_n}/{n}]" if think_n < n else "")
        )
        if vis_n:
            lines.append(
                f"  visible_tps_phase:   {fmt(vis_med, vis_q1, vis_q3, ' tok/s', vis_iqr)}"
                f"  [streaming only, n={vis_n}/{n}]"
            )
        if truncated:
            lines.append(
                f"  WARNING: {truncated}/{n} trial(s) hit max_tokens (stop_reason="
                '"max_tokens") -- generation was cut off, not a natural stop. '
                "wall_s/tok_s above still reflect real decode rate, but the "
                "model's natural total-output length for this condition is "
                "UNKNOWN (it wanted to keep generating). Re-run with a higher "
                "--max-tokens for a clean natural-completion read."
            )
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results_path", help="path to a run_sweep.py results JSONL file")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    records = load_records(args.results_path)
    if not records:
        print(f"no records found in {args.results_path}", file=sys.stderr)
        sys.exit(1)
    groups = group_by_label(records)
    print(report(groups))
