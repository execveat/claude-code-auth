#!/usr/bin/env python3
"""Generic experiment-sweep runner for bench/synthetic_multiturn_test.py.

Takes a sweep spec (JSON) listing conditions, e.g.:

    {
      "prompt": "optional global default prompt for every condition",
      "conditions": [
        {"label": "effort-low", "args": {"effort": "low", "thinking": "adaptive"}},
        {"label": "effort-high", "args": {"effort": "high", "thinking": "adaptive"}}
      ]
    }

Each condition's `args` maps to synthetic_multiturn_test.py's CLI flags
(mode, prompt, model, max_tokens, thinking, thinking_budget, effort, speed,
service_tier, inference_geo, extra_beta, cache_ttl) -- anything omitted
falls back to that script's own defaults, then to the spec's top-level
defaults, then to the condition's own args (most specific wins).

Runs `--trials-per-condition` trials per condition, SERIALLY -- one real API
call at a time, never concurrent. This is deliberate: every single-lever
experiment this tool runs (effort sweep, redact-thinking A/B, etc.) assumes
no OTHER live traffic is hitting the account at the same time, or the
concurrency question itself (a SEPARATE, not-yet-run experiment) would
silently contaminate the result. See docs/THROUGHPUT_RESEARCH.md's
Methodology Notes for the full rationale.

Calls synthetic_multiturn_test.py's build_messages/run_streaming/
run_nonstreaming functions DIRECTLY (no subprocess) -- this file lives next
to it in bench/, so it's imported as a plain sibling module (Python adds a
script's own directory to sys.path automatically).

--dry-run prints the fully resolved trial plan (every condition x trial,
with its resolved args) and makes ZERO network calls -- it never even
imports/calls run_streaming/run_nonstreaming, so there is no code path by
which --dry-run could accidentally hit the network.

Usage:
    uv run python bench/run_sweep.py --spec bench/specs/effort_sweep.json --dry-run
    uv run python bench/run_sweep.py --spec bench/specs/effort_sweep.json \\
        --trials-per-condition 3 --delay-seconds 5 --out bench/results/effort_sweep.jsonl

Requires bench/fixtures/synthetic_history.json to exist for any LIVE run
(not for --dry-run) -- see bench/README.md.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import requests

import synthetic_multiturn_test as smt

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "bench" / "results" / "sweep.jsonl"

# Mirrors synthetic_multiturn_test.py's own argparse defaults exactly, so a
# condition that specifies nothing behaves identically to running that
# script with no flags at all.
DEFAULT_ARGS = {
    "mode": "stream",
    "prompt": (
        "List exactly 60 distinct, real, verifiable facts about deep-sea "
        "creatures. One fact per line. No headers, no numbering, no commentary."
    ),
    "model": "claude-sonnet-5",
    "max_tokens": 20000,
    "thinking": "adaptive",
    "thinking_budget": 8000,
    "effort": None,
    "speed": None,
    "service_tier": None,
    "inference_geo": None,
    "extra_beta": [],
    "cache_ttl": "1h",
}


def load_spec(path):
    with open(path) as f:
        return json.load(f)


def resolve_condition_args(spec, condition):
    """Layer defaults (least to most specific): this script's DEFAULT_ARGS,
    then the spec's own top-level defaults (any key besides "conditions"),
    then the condition's own "args" dict.
    """
    resolved = dict(DEFAULT_ARGS)
    spec_defaults = {k: v for k, v in spec.items() if k != "conditions"}
    resolved.update(spec_defaults)
    resolved.update(condition.get("args", {}))
    return resolved


def plan_trials(spec, trials_per_condition):
    """Return [(label, trial_index, resolved_args_dict), ...] in a fixed,
    deterministic order (condition order from the spec, trials 0..N-1 within
    each). Pure function -- no I/O, no network -- so --dry-run and the real
    run share the exact same planning code path.
    """
    plan = []
    for condition in spec["conditions"]:
        label = condition["label"]
        resolved = resolve_condition_args(spec, condition)
        for trial_idx in range(trials_per_condition):
            plan.append((label, trial_idx, resolved))
    return plan


def run_trial(resolved_args, session=None):
    """The ONLY function in this module that touches the network. Builds a
    SimpleNamespace matching what synthetic_multiturn_test.py's own argparse
    Namespace would look like, then calls its build_messages/run_streaming/
    run_nonstreaming directly. session=None (default) preserves exact current
    behavior (fresh requests.post per call) -- see synthetic_multiturn_test.py's
    run_streaming/run_nonstreaming docstrings (TASK-020).
    """
    args = SimpleNamespace(**resolved_args)
    cache_ttl = None if args.cache_ttl == "none" else args.cache_ttl
    messages = smt.build_messages(args.prompt, cache_ttl)
    if args.mode == "stream":
        return smt.run_streaming(messages, args, session=session)
    return smt.run_nonstreaming(messages, args, session=session)


def parse_cli_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spec", required=True, help="path to a sweep spec JSON file")
    p.add_argument("--trials-per-condition", type=int, default=2)
    p.add_argument(
        "--delay-seconds",
        type=float,
        default=3.0,
        help="pause between real trials (ignored in --dry-run)",
    )
    p.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="JSONL file to append results to (default bench/results/sweep.jsonl)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned trial list and exit -- makes ZERO network calls",
    )
    p.add_argument(
        "--reuse-connection",
        action="store_true",
        help="share ONE requests.Session (persistent TCP/TLS connection) across "
        "every trial in this sweep, instead of a fresh connection per trial "
        "(TASK-020: does connection reuse shrink per-request fixed overhead?). "
        "Default off -- omitting this flag is byte-identical to today's behavior.",
    )
    return p.parse_args()


def main():
    cli_args = parse_cli_args()
    spec = load_spec(cli_args.spec)
    plan = plan_trials(spec, cli_args.trials_per_condition)

    if cli_args.dry_run:
        for i, (label, trial_idx, resolved) in enumerate(plan):
            print(
                f"[{i + 1}/{len(plan)}] label={label!r} trial={trial_idx} args={resolved}"
            )
        print(
            f"\nTotal planned trials: {len(plan)} -- zero network calls made (--dry-run)."
        )
        return

    out_path = Path(cli_args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # One shared Session for the whole sweep when --reuse-connection is set;
    # None (the default) means every run_trial call falls through to a fresh
    # requests.post, identical to pre-TASK-020 behavior.
    session = requests.Session() if cli_args.reuse_connection else None
    with open(out_path, "a") as f:
        for i, (label, trial_idx, resolved) in enumerate(plan):
            print(
                f"[{i + 1}/{len(plan)}] running label={label!r} trial={trial_idx}...",
                file=sys.stderr,
            )
            result = run_trial(resolved, session=session)
            record = {
                "label": label,
                "trial": trial_idx,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "args": resolved,
                "result": result,
            }
            f.write(json.dumps(record) + "\n")
            f.flush()
            if i < len(plan) - 1:
                time.sleep(cli_args.delay_seconds)
    print(f"wrote {len(plan)} trial(s) to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
