#!/usr/bin/env python3
"""Cold-cache concurrency stampede (TASK-023, peer-review-surfaced follow-up to F13).

F13 (bench/concurrency_test.py) tested N=1/2/4/8/16 simultaneous same-account
calls, but entirely on a WARM cache lineage -- the shared prefix was already
cached before any concurrency trial ran. Untested: what happens when N
concurrent calls all race to write the SAME COLD prefix at once? Does the API
dedupe concurrent writers to one cache key (one call pays cache_creation, the
rest get cache_read), or does each concurrent caller independently pay the
full cache-write cost (redundant, worse economics than F13's warm scenario)?

Design: ALL N calls in one trial share the SAME unique marker (same technique
as bench/cache_ttl_gap_test.py, but here the whole point is N threads racing
the identical marker/cache-key at once, not N independent markers). Fired via
a thread pool so they genuinely overlap in flight.

Usage:
    uv run python bench/cold_cache_stampede_test.py --n 4 --trials 2
    uv run python bench/cold_cache_stampede_test.py --dry-run
"""

import argparse
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import synthetic_multiturn_test as smt

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "bench" / "results" / "task023_cold_cache_stampede.jsonl"

TRIVIAL_PROMPT = "Reply with exactly the word OK and nothing else."


def build_marked_messages(marker):
    """Same content-addressed-cache-isolation technique as cache_ttl_gap_test.py:
    inject a unique marker into the cached block's TEXT so this trial's key is
    guaranteed never-before-seen. Here every call in a trial shares the SAME
    marker -- they are all racing the identical cold cache entry at once."""
    turns = smt.load_shared_prefix()
    last_block = turns[-1]["content"][-1]
    last_block["text"] = (
        last_block.get("text", "") + f"\n\n[cold-cache-stampede marker: {marker}]"
    )
    last_block["cache_control"] = {"type": "ephemeral", "ttl": "5m"}
    turns.append(
        {"role": "user", "content": [{"type": "text", "text": TRIVIAL_PROMPT}]}
    )
    return turns


def build_args():
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
        extra_beta=[],
        cache_ttl=None,
    )


def one_call(marker, args, call_idx):
    messages = build_marked_messages(marker)
    t0 = time.time()
    try:
        result = smt.run_nonstreaming(messages, args)
        error = None
    except Exception as e:  # noqa: BLE001 -- deliberately broad: we want to
        # OBSERVE any race-condition symptom (timeout, malformed response,
        # rate limit), not just the happy path.
        result = None
        error = f"{type(e).__name__}: {e}"
    t1 = time.time()
    return {
        "call_idx": call_idx,
        "call_start": t0,
        "call_end": t1,
        "result": result,
        "error": error,
    }


def run_stampede(n, trial_idx, args):
    """N concurrent calls, ALL sharing one marker -- a genuine race for the
    same cold cache key."""
    marker = str(uuid.uuid4())
    print(
        f"[trial {trial_idx}] N={n} marker={marker[:8]}... firing concurrently",
        file=sys.stderr,
    )
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(one_call, marker, args, i) for i in range(n)]
        records = [f.result() for f in as_completed(futures)]
    records.sort(key=lambda r: r["call_idx"])
    return marker, records


def usage_row(r):
    if r["result"] is None:
        return {"error": r["error"]}
    u = (r["result"].get("usage")) or {}
    return {
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": u.get("cache_read_input_tokens"),
        "input_tokens": u.get("input_tokens"),
        "wall_ms": r["result"]["wall_ms"],
        "error": None,
    }


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--n", type=int, default=4, help="concurrent calls per stampede trial"
    )
    p.add_argument(
        "--trials", type=int, default=2, help="independent stampede trials to run"
    )
    p.add_argument("--out", default=str(DEFAULT_OUT))
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
            f"planned: {cli.trials} trial(s) x N={cli.n} concurrent calls, all sharing one marker/trial"
        )
        print("zero network calls made (--dry-run).")
        return

    out_path = Path(cli.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = build_args()

    all_trials = []
    with open(out_path, "a") as f:
        for trial_idx in range(cli.trials):
            marker, records = run_stampede(cli.n, trial_idx, args)
            rows = [usage_row(r) for r in records]
            for i, row in enumerate(rows):
                print(
                    f"  call={i} cache_create={row.get('cache_creation_input_tokens')} "
                    f"cache_read={row.get('cache_read_input_tokens')} error={row.get('error')}",
                    file=sys.stderr,
                )
            record = {
                "experiment": "task023_cold_cache_stampede",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trial": trial_idx,
                "n": cli.n,
                "marker": marker,
                "calls": rows,
            }
            f.write(json.dumps(record) + "\n")
            f.flush()
            all_trials.append(record)
            if trial_idx < cli.trials - 1:
                time.sleep(2)

    print(f"\nwrote results to {out_path}", file=sys.stderr)
    print("\n=== SUMMARY ===", file=sys.stderr)
    for t in all_trials:
        creates = sum(1 for c in t["calls"] if c.get("cache_creation_input_tokens"))
        reads = sum(1 for c in t["calls"] if c.get("cache_read_input_tokens"))
        errors = sum(1 for c in t["calls"] if c.get("error"))
        print(
            f"trial={t['trial']} N={t['n']} -> {creates} paid cache_creation, "
            f"{reads} got cache_read, {errors} errored",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
