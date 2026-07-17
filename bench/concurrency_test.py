#!/usr/bin/env python3
"""Concurrency experiment: does per-request tok/s degrade under N parallel
raw API calls from the same OAuth account?

Fires N calls SIMULTANEOUSLY (via a thread pool -- `requests` releases the
GIL during network I/O, so this genuinely overlaps N in-flight HTTP calls
from one Python process) with identical args (same prompt/effort/thinking),
then reports BOTH:
  - per-request tok/s at this concurrency level (does each individual
    call's own throughput degrade as N rises?)
  - aggregate tok/s across the whole batch (total output tokens / batch
    wall-clock span, from first call's start to last call's finish) --
    does concurrency increase how much real work gets done per second
    overall, even if each individual call slows down a bit?

This is DELIBERATELY THE ONLY EXPERIMENT in bench/ that calls the API
concurrently -- every other tool here (run_sweep.py) is serial by design
specifically so it never contaminates this one, and vice versa this must
run in its own isolated window with nothing else hitting the account.
See docs/THROUGHPUT_RESEARCH.md's Methodology Notes.

Usage:
    uv run python bench/concurrency_test.py --levels 1,2,4 --out bench/results/concurrency.jsonl
    uv run python bench/concurrency_test.py --levels 1,2,4,8 --effort high --dry-run
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import synthetic_multiturn_test as smt

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "bench" / "results" / "concurrency.jsonl"

DEFAULT_PROMPT = (
    "List exactly 30 distinct, real, verifiable facts about deep-sea "
    "creatures. One fact per line. No headers, no numbering, no commentary."
)


def build_args(effort, thinking, max_tokens, cache_ttl):
    return SimpleNamespace(
        mode="nonstream",
        prompt=DEFAULT_PROMPT,
        model="claude-sonnet-5",
        max_tokens=max_tokens,
        thinking=thinking,
        thinking_budget=8000,
        effort=effort,
        speed=None,
        service_tier=None,
        inference_geo=None,
        extra_beta=[],
        cache_ttl=cache_ttl,
    )


def one_call(args, cache_ttl):
    """Runs in a worker thread. Builds its own messages (cheap, no network)
    and makes exactly one non-streaming API call, timing it locally so each
    thread's own start/end are precise regardless of scheduling jitter."""
    cache_ttl_resolved = None if cache_ttl == "none" else cache_ttl
    messages = smt.build_messages(args.prompt, cache_ttl_resolved)
    t0 = time.time()
    result = smt.run_nonstreaming(messages, args)
    t1 = time.time()
    return {"call_start": t0, "call_end": t1, "result": result}


def run_level(n, args, cache_ttl):
    """Fire n calls at once via a thread pool, return per-call records plus
    the batch's own wall-clock span (first start to last finish)."""
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(one_call, args, cache_ttl) for _ in range(n)]
        records = [f.result() for f in as_completed(futures)]
    batch_start = min(r["call_start"] for r in records)
    batch_end = max(r["call_end"] for r in records)
    return records, batch_end - batch_start


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--levels",
        default="1,2,4",
        help="comma-separated concurrency levels to test, e.g. 1,2,4,8",
    )
    p.add_argument("--effort", default="high")
    p.add_argument("--thinking", default="adaptive")
    p.add_argument("--max-tokens", type=int, default=6000)
    p.add_argument("--cache-ttl", default="1h")
    p.add_argument(
        "--delay-seconds",
        type=float,
        default=5.0,
        help="pause between concurrency levels (not between calls within a level)",
    )
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned levels and exit -- zero network calls",
    )
    return p.parse_args()


def main():
    cli = parse_args()
    levels = [int(x) for x in cli.levels.split(",")]

    if cli.dry_run:
        total_calls = sum(levels)
        print(f"planned concurrency levels: {levels}")
        print(f"total calls across all levels: {total_calls}")
        print(
            f"effort={cli.effort!r} thinking={cli.thinking!r} "
            f"max_tokens={cli.max_tokens} cache_ttl={cli.cache_ttl!r}"
        )
        print("zero network calls made (--dry-run).")
        return

    args = build_args(cli.effort, cli.thinking, cli.max_tokens, cli.cache_ttl)
    out_path = Path(cli.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "a") as f:
        for i, n in enumerate(levels):
            print(f"running concurrency level n={n}...", file=sys.stderr)
            records, batch_span_s = run_level(n, args, cli.cache_ttl)
            total_output = sum(r["result"]["output_tokens"] or 0 for r in records)
            record = {
                "level": n,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "args": vars(args),
                "batch_span_s": batch_span_s,
                "aggregate_tok_s": (
                    total_output / batch_span_s if batch_span_s else None
                ),
                "calls": [r["result"] for r in records],
            }
            f.write(json.dumps(record) + "\n")
            f.flush()
            print(
                f"  level={n}: batch_span={batch_span_s:.1f}s "
                f"total_output={total_output} "
                f"aggregate_tok/s={record['aggregate_tok_s']:.1f}",
                file=sys.stderr,
            )
            if i < len(levels) - 1:
                time.sleep(cli.delay_seconds)
    print(f"wrote {len(levels)} level(s) to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
