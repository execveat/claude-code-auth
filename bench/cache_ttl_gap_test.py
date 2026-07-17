#!/usr/bin/env python3
"""Cache TTL session-gap A/B (TASK-013).

Question: does `cache_control.ttl:"1h"` genuinely keep a prompt-cache entry
alive across a real wall-clock idle gap that would expire a `"5m"` entry?
Ground truth is `usage.cache_creation_input_tokens` (miss/rewrite) vs
`usage.cache_read_input_tokens` (hit) on a follow-up call after the gap --
NOT TTFT, which is noisier and confounded by adaptive-thinking variance
(see docs/THROUGHPUT_RESEARCH.md F19 for why a TTFT-only comparison burned
this mission before).

Design hazard this script exists to avoid: synthetic_multiturn_test.py's
build_messages() always marks cache_control on the IDENTICAL last block of
the on-disk shared prefix. Anthropic's cache key is content-addressed, so
any two calls sharing that exact prefix text collide onto ONE cache entry
regardless of which ttl each individually requests -- whichever call writes
first sets the entry's real expiry, contaminating every other trial/condition
reusing the same content. Fix: inject a unique per-trial marker into the
cached block's text before setting cache_control, so every trial gets its
own independent cache entry (same technique TASK-015 used for structured-
output cold/warm isolation, see F18).

Efficiency: since every trial's cache entry is independent, all trials'
"call #1"s can be fired concurrently, then ONE shared real gap is slept,
then all "call #2"s fired concurrently -- collapsing wall-clock from
~40min (naive serial-per-trial) to ~1 gap-duration (~7-10min total).

Usage:
    uv run python bench/cache_ttl_gap_test.py --gap-seconds 420
    uv run python bench/cache_ttl_gap_test.py --dry-run
"""

import argparse
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import synthetic_multiturn_test as smt

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "bench" / "results" / "task013_cache_ttl_gap.jsonl"

TRIVIAL_PROMPT = "Reply with exactly the word OK and nothing else."


def build_marked_messages(marker, cache_ttl, scope=None):
    """Same shape as smt.build_messages, but injects a unique marker into the
    cached block's TEXT (not just metadata) so this trial gets its own
    independent, content-addressed cache entry -- never colliding with any
    other trial/condition in this experiment or any other bench/ run."""
    turns = smt.load_shared_prefix()
    last_block = turns[-1]["content"][-1]
    last_block["text"] = (
        last_block.get("text", "") + f"\n\n[cache-ttl-probe marker: {marker}]"
    )
    cache_control = {"type": "ephemeral", "ttl": cache_ttl}
    if scope:
        cache_control["scope"] = scope
    last_block["cache_control"] = cache_control
    turns.append(
        {"role": "user", "content": [{"type": "text", "text": TRIVIAL_PROMPT}]}
    )
    return turns


def build_args(extra_beta=None):
    return SimpleNamespace(
        mode="nonstream",
        model=smt.DEFAULT_MODEL,
        max_tokens=50,
        thinking="disabled",
        thinking_budget=8000,
        thinking_display=None,
        effort=None,
        speed=None,
        service_tier=None,
        inference_geo=None,
        output_schema=None,
        extra_beta=extra_beta or [],
        cache_ttl=None,  # unused: we build messages ourselves via build_marked_messages
    )


def one_call(messages, args):
    t0 = time.time()
    result = smt.run_nonstreaming(messages, args)
    t1 = time.time()
    return {"call_start": t0, "call_end": t1, "result": result}


def usage_row(r):
    u = (r["result"].get("usage")) or {}
    return {
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": u.get("cache_read_input_tokens"),
        "input_tokens": u.get("input_tokens"),
        "wall_ms": r["result"]["wall_ms"],
    }


def run_main_experiment(gap_seconds, n_per_condition, out_f):
    conditions = ["5m", "1h"]
    trials = []
    for cond in conditions:
        for i in range(n_per_condition):
            trials.append({"condition": cond, "trial": i, "marker": str(uuid.uuid4())})

    print(
        f"[main] {len(trials)} trials ({n_per_condition}/condition x {len(conditions)} conditions)",
        file=sys.stderr,
    )

    args = build_args()

    def fire_call1(t):
        messages = build_marked_messages(t["marker"], t["condition"])
        return one_call(messages, args)

    print("[main] firing all call#1s concurrently...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=len(trials)) as pool:
        call1_results = list(pool.map(fire_call1, trials))

    for t, r in zip(trials, call1_results):
        t["call1"] = usage_row(r)
        t["call1_end_ts"] = r["call_end"]
        print(
            f"  call1 cond={t['condition']} trial={t['trial']} "
            f"cache_create={t['call1']['cache_creation_input_tokens']} "
            f"cache_read={t['call1']['cache_read_input_tokens']}",
            file=sys.stderr,
        )

    print(
        f"[main] sleeping {gap_seconds}s (the real wall-clock gap)...", file=sys.stderr
    )
    time.sleep(gap_seconds)

    def fire_call2(t):
        messages = build_marked_messages(t["marker"], t["condition"])
        return one_call(messages, args)

    print("[main] firing all call#2s concurrently...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=len(trials)) as pool:
        call2_results = list(pool.map(fire_call2, trials))

    for t, r in zip(trials, call2_results):
        t["call2"] = usage_row(r)
        t["actual_gap_s"] = r["call_start"] - t["call1_end_ts"]
        print(
            f"  call2 cond={t['condition']} trial={t['trial']} "
            f"actual_gap_s={t['actual_gap_s']:.1f} "
            f"cache_create={t['call2']['cache_creation_input_tokens']} "
            f"cache_read={t['call2']['cache_read_input_tokens']}",
            file=sys.stderr,
        )
        record = {
            "experiment": "task013_cache_ttl_gap",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in t.items() if k != "call1_end_ts"},
        }
        out_f.write(json.dumps(record) + "\n")
    out_f.flush()
    return trials


def run_scope_probe(out_f):
    """Bonus: is cache_control.scope:'global' (sent via the undocumented
    prompt-caching-scope-2026-01-05 beta) even accepted for a single-user
    OAuth caller, and does it change response/usage shape at all?"""
    print(
        "[bonus] scope probe: baseline (no scope) vs scope='global'...", file=sys.stderr
    )
    baseline_marker = str(uuid.uuid4())
    scope_marker = str(uuid.uuid4())

    baseline_args = build_args()
    baseline_messages = build_marked_messages(baseline_marker, "5m")
    try:
        baseline = one_call(baseline_messages, baseline_args)
        baseline_status = "200"
        baseline_usage = usage_row(baseline)
    except RuntimeError as e:
        baseline_status = str(e)[:300]
        baseline_usage = None

    scope_args = build_args(extra_beta=["prompt-caching-scope-2026-01-05"])
    scope_messages = build_marked_messages(scope_marker, "5m", scope="global")
    try:
        scoped = one_call(scope_messages, scope_args)
        scope_status = "200"
        scope_usage = usage_row(scoped)
    except RuntimeError as e:
        scope_status = str(e)[:300]
        scope_usage = None

    record = {
        "experiment": "task013_scope_global_probe",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "baseline_status": baseline_status,
        "baseline_usage": baseline_usage,
        "scope_global_status": scope_status,
        "scope_global_usage": scope_usage,
    }
    out_f.write(json.dumps(record) + "\n")
    out_f.flush()
    print(
        f"[bonus] baseline_status={baseline_status} baseline_usage={baseline_usage}",
        file=sys.stderr,
    )
    print(
        f"[bonus] scope_status={scope_status} scope_usage={scope_usage}",
        file=sys.stderr,
    )
    return record


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--gap-seconds",
        type=int,
        default=420,
        help="real wall-clock idle gap between call#1 and call#2 (default 420s = 7min, "
        "past the 5m TTL boundary, well under the 1h one)",
    )
    p.add_argument("--n-per-condition", type=int, default=3)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--skip-scope-probe", action="store_true")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned trials and exit -- zero network calls",
    )
    return p.parse_args()


def main():
    cli = parse_args()
    if cli.dry_run:
        print(
            f"planned: {cli.n_per_condition} trials x 2 conditions (5m, 1h), "
            f"gap={cli.gap_seconds}s, scope_probe={not cli.skip_scope_probe}"
        )
        print("zero network calls made (--dry-run).")
        return

    out_path = Path(cli.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "a") as f:
        trials = run_main_experiment(cli.gap_seconds, cli.n_per_condition, f)
        if not cli.skip_scope_probe:
            run_scope_probe(f)

    print(f"\nwrote results to {out_path}", file=sys.stderr)

    print("\n=== SUMMARY ===", file=sys.stderr)
    for t in trials:
        c1 = t["call1"]
        c2 = t["call2"]
        print(
            f"cond={t['condition']} trial={t['trial']} gap={t['actual_gap_s']:.0f}s | "
            f"call1: create={c1['cache_creation_input_tokens']} read={c1['cache_read_input_tokens']} | "
            f"call2: create={c2['cache_creation_input_tokens']} read={c2['cache_read_input_tokens']}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
