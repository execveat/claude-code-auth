#!/usr/bin/env python3
"""Concurrency experiment, HTTP/2 variant: does multiplexing N concurrent
calls over ONE HTTP/2 connection (via `httpx`) recover F13's N=16 sub-linear
aggregate-throughput shortfall (13.4x, not 16x), which F13 attributed to "a
client-side effect (thread scheduling / connection-pool limits in this
harness)" without isolating it?

`bench/concurrency_test.py` (the original, requests/HTTP1.1-based tool) opens
one TCP socket per concurrent thread -- `requests.Session()` pooling does not
change this, since each thread still gets its own connection out of the pool
under real concurrent load. HTTP/2 multiplexes many concurrent streams over a
SINGLE connection, which could either (a) recover the shortfall if it was
connection/socket-count-limited, or (b) show no difference if the shortfall
is actually server-side (e.g. mild natural per-request latency variance, as
F13 itself suggested was equally plausible).

This is DISTINCT from F14 (connection/session reuse): F14 tested reusing one
`requests.Session()` SERIALLY, one call at a time. This tests true CONCURRENT
multiplexing -- many simultaneous streams sharing one HTTP/2 connection.

Mirrors bench/concurrency_test.py's structure and output-record shape closely
so results are directly comparable; only the HTTP layer differs (one shared
httpx.Client(http2=True) instead of N independent `requests` connections).

Usage:
    uv run python bench/concurrency_http2_test.py --levels 16 --out bench/results/concurrency_http2.jsonl
    uv run python bench/concurrency_http2_test.py --levels 16,24 --dry-run
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

import synthetic_multiturn_test as smt

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "bench" / "results" / "concurrency_http2.jsonl"

# Same prompt as bench/concurrency_test.py for direct comparability.
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


def run_nonstreaming_httpx(messages, args, client):
    """httpx/HTTP2 equivalent of synthetic_multiturn_test.run_nonstreaming --
    same body/header construction (smt.build_body, so the mandatory OAuth
    attribution system block is identical), but posts via a SHARED
    httpx.Client(http2=True) instead of `requests`, so N concurrent calls
    genuinely multiplex over one connection rather than opening N sockets."""
    body, headers = smt.build_body(messages, args)
    t_start = time.time()
    resp = client.post(smt.URL, headers=headers, json=body, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:2000]}")
    t_end = time.time()
    data = resp.json()
    usage = data.get("usage") or {}
    return {
        "mode": "non-stream-http2",
        "wall_ms": (t_end - t_start) * 1000,
        "output_tokens": usage.get("output_tokens"),
        "stop_reason": data.get("stop_reason"),
        "usage": usage,
        "http_version": resp.http_version,
    }


def one_call(args, cache_ttl, client):
    cache_ttl_resolved = None if cache_ttl == "none" else cache_ttl
    messages = smt.build_messages(args.prompt, cache_ttl_resolved)
    t0 = time.time()
    result = run_nonstreaming_httpx(messages, args, client)
    t1 = time.time()
    return {"call_start": t0, "call_end": t1, "result": result}


def run_level(n, args, cache_ttl):
    """Fire n calls at once via a thread pool, ALL sharing ONE
    httpx.Client(http2=True) -- this is the key difference from
    concurrency_test.py's per-thread `requests` connections. httpx.Client is
    documented thread-safe for concurrent use from multiple threads."""
    with httpx.Client(http2=True) as client:
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(one_call, args, cache_ttl, client) for _ in range(n)]
            records = [f.result() for f in as_completed(futures)]
    batch_start = min(r["call_start"] for r in records)
    batch_end = max(r["call_end"] for r in records)
    return records, batch_end - batch_start


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--levels",
        default="16",
        help="comma-separated concurrency levels to test, e.g. 16,24,32",
    )
    p.add_argument(
        "--effort", choices=["low", "medium", "high", "xhigh", "max"], default="high"
    )
    p.add_argument(
        "--thinking", choices=["enabled", "adaptive", "disabled"], default="adaptive"
    )
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
        print(f"planned concurrency levels (HTTP/2, shared client): {levels}")
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
            print(f"running HTTP/2 concurrency level n={n}...", file=sys.stderr)
            records, batch_span_s = run_level(n, args, cli.cache_ttl)
            total_output = sum(r["result"]["output_tokens"] or 0 for r in records)
            http_versions = {r["result"]["http_version"] for r in records}
            record = {
                "level": n,
                "protocol": "http2-shared-client",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "args": vars(args),
                "batch_span_s": batch_span_s,
                "aggregate_tok_s": (
                    total_output / batch_span_s if batch_span_s else None
                ),
                "http_versions_observed": sorted(http_versions),
                "calls": [r["result"] for r in records],
            }
            f.write(json.dumps(record) + "\n")
            f.flush()
            print(
                f"  level={n}: batch_span={batch_span_s:.1f}s "
                f"total_output={total_output} "
                f"aggregate_tok/s={record['aggregate_tok_s']:.1f} "
                f"http_versions={sorted(http_versions)}",
                file=sys.stderr,
            )
            if i < len(levels) - 1:
                time.sleep(cli.delay_seconds)
    print(f"wrote {len(levels)} level(s) to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
