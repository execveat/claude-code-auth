#!/usr/bin/env python3
"""Synthetic multi-turn realistic-context throughput test.

Uses a real, structurally-valid ~200-350K token conversation history window
(built by build_synthetic_history.py from a real Claude Code session
transcript) as a shared prefix, cache_control-marked so it's written once
and read cheaply on every subsequent trial. Appends ONE new trailing user
message (varied between trials, never touching the shared prefix) and sends
via raw API using claude-code-auth -- bypassing the Claude Code CLI/harness
entirely (no --resume/--fork-session, so no risk of the model inheriting
"stop and ask permission" framing from the resumed session's own history).

Supports both the legacy manual thinking mode (`thinking.budget_tokens`) and
the newer adaptive mode (`thinking.type=adaptive` + `output_config.effort`).
Docs claim manual `enabled` mode returns HTTP 400 on Sonnet 5/Opus 4.7/4.8 --
if that's true for OUR OAuth-authenticated calls specifically is exactly what
this harness exists to check; don't assume either the docs or a stale prior
finding without a fresh, deliberate probe (see bench/README.md's "resolving
the thinking-mode contradiction" section).

Extended thinking, when enabled in either mode, lets us separate TOTAL output
tokens (thinking + visible text, what usage.output_tokens actually reports)
from VISIBLE-ONLY output tokens (text blocks only -- what a person watching
the terminal actually sees). Reports both tok/s figures plus a time
breakdown: time-to-first-thinking-token vs time-to-first-VISIBLE-text-token,
so we can see exactly how much wall-clock is invisible thinking before any
visible output starts.

Usage:
    uv run python bench/synthetic_multiturn_test.py --mode stream --prompt "..."
    uv run python bench/synthetic_multiturn_test.py --mode nonstream --thinking adaptive --effort high
    uv run python bench/synthetic_multiturn_test.py --mode stream --thinking enabled --thinking-budget 8000
    uv run python bench/synthetic_multiturn_test.py --mode stream --thinking disabled --effort low
    uv run python bench/synthetic_multiturn_test.py --model claude-opus-4-8 --extra-beta fast-mode-2026-02-01 --speed fast

Requires bench/fixtures/synthetic_history.json to exist -- see bench/README.md
to build it (gitignored; not checked in, since it's a verbatim slice of a
real conversation).
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

from claude_code_auth import ClaudeCodeOAuthManager

URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"
REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = REPO_ROOT / "bench" / "fixtures" / "synthetic_history.json"
DEFAULT_MAX_TOKENS = 20000


def load_shared_prefix():
    if not HISTORY_PATH.exists():
        raise SystemExit(
            f"{HISTORY_PATH} not found -- build it first with "
            f"build_synthetic_history.py (see bench/README.md)"
        )
    with open(HISTORY_PATH) as f:
        turns = json.load(f)
    # Deep copy via round-trip so mutating (cache_control) never touches the
    # on-disk canonical copy shared across trials.
    return json.loads(json.dumps(turns))


def first_user_message_text(messages):
    for m in messages:
        if m["role"] != "user":
            continue
        c = m["content"]
        if isinstance(c, str):
            return c
        for b in c:
            if b.get("type") == "text":
                return b.get("text", "")
    return ""


def build_messages(trailing_prompt, cache_ttl):
    turns = load_shared_prefix()
    if cache_ttl:
        last_block = turns[-1]["content"][-1]
        last_block["cache_control"] = {"type": "ephemeral", "ttl": cache_ttl}
    turns.append(
        {"role": "user", "content": [{"type": "text", "text": trailing_prompt}]}
    )
    return turns


def build_thinking_block(args):
    if args.thinking == "disabled":
        return None
    if args.thinking == "enabled":
        return {"type": "enabled", "budget_tokens": args.thinking_budget}
    if args.thinking == "adaptive":
        return {"type": "adaptive"}
    raise ValueError(f"unknown thinking mode {args.thinking!r}")


def build_body(messages, args):
    manager = ClaudeCodeOAuthManager()
    text = first_user_message_text(messages)
    system = manager.build_system_blocks(text)

    body = {
        "model": args.model,
        "max_tokens": args.max_tokens,
        "stream": args.mode == "stream",
        "system": system,
        "messages": messages,
    }

    thinking_block = build_thinking_block(args)
    if thinking_block is not None:
        body["thinking"] = thinking_block

    output_config = {}
    if args.effort:
        output_config["effort"] = args.effort
    if output_config:
        body["output_config"] = output_config

    if args.speed:
        body["speed"] = args.speed
    if args.service_tier:
        body["service_tier"] = args.service_tier
    if args.inference_geo:
        body["inference_geo"] = args.inference_geo

    headers = manager.build_headers()
    if args.extra_beta:
        existing = headers.get("anthropic-beta", "")
        values = [v for v in existing.split(",") if v] + args.extra_beta
        headers["anthropic-beta"] = ",".join(
            dict.fromkeys(values)
        )  # de-dup, keep order

    return body, headers


def run_streaming(messages, args, session=None):
    """session=None (default) preserves today's exact behavior -- a fresh
    module-level requests.post() per call, no connection reuse. Pass a
    requests.Session() to reuse the same TCP/TLS connection across calls
    (TASK-020: does connection/session reuse shrink the ~8-12s fixed
    per-request overhead implied by F10/F12?)."""
    body, headers = build_body(messages, args)
    poster = session.post if session is not None else requests.post
    t_start = time.time()
    t_first_any = None
    t_first_thinking = None
    t_first_text = None
    thinking_chars = 0
    text_chars = 0
    output_tokens = None
    stop_reason = None
    usage_final = None

    with poster(URL, headers=headers, json=body, stream=True, timeout=300) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:2000]}")
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data:"):
                continue
            payload = raw_line[len("data:") :].strip()
            if not payload:
                continue
            evt = json.loads(payload)
            etype = evt.get("type")
            now = time.time()

            if etype == "content_block_delta":
                if t_first_any is None:
                    t_first_any = now
                delta = evt.get("delta", {})
                dtype = delta.get("type")
                if dtype == "thinking_delta":
                    if t_first_thinking is None:
                        t_first_thinking = now
                    thinking_chars += len(delta.get("thinking", ""))
                elif dtype == "text_delta":
                    if t_first_text is None:
                        t_first_text = now
                    text_chars += len(delta.get("text", ""))
            elif etype == "message_delta":
                # MERGE, don't replace: message_delta's usage object carries
                # updated output_tokens/output_tokens_details but does NOT
                # repeat message_start-only fields like service_tier -- a
                # wholesale replace here silently drops them from every
                # streaming trial's usage record (found via external peer
                # review, 2026-07-17: confirmed empirically, 6/6 streaming
                # trials in bench/results/task011_stream_vs_nonstream.jsonl
                # had usage.service_tier == "" while their non-streaming
                # siblings correctly showed "standard").
                delta_usage = evt.get("usage")
                if delta_usage:
                    usage_final = {**(usage_final or {}), **delta_usage}
                if usage_final and "output_tokens" in usage_final:
                    output_tokens = usage_final["output_tokens"]
                stop_reason = (evt.get("delta") or {}).get("stop_reason", stop_reason)
            elif etype == "message_start":
                usage_final = (evt.get("message") or {}).get("usage")

    t_end = time.time()
    return {
        "mode": "stream",
        "wall_ms": (t_end - t_start) * 1000,
        "ttft_any_ms": (t_first_any - t_start) * 1000 if t_first_any else None,
        "ttft_thinking_ms": (t_first_thinking - t_start) * 1000
        if t_first_thinking
        else None,
        "ttft_text_ms": (t_first_text - t_start) * 1000 if t_first_text else None,
        "thinking_chars": thinking_chars,
        "text_chars": text_chars,
        "output_tokens": output_tokens,
        "stop_reason": stop_reason,
        "usage": usage_final,
    }


def run_nonstreaming(messages, args, session=None):
    """session=None (default) preserves today's exact behavior -- see
    run_streaming's docstring for the rationale (TASK-020)."""
    body, headers = build_body(messages, args)
    poster = session.post if session is not None else requests.post
    t_start = time.time()
    resp = poster(URL, headers=headers, json=body, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:2000]}")
    t_end = time.time()
    data = resp.json()
    usage = data.get("usage") or {}
    thinking_chars = 0
    text_chars = 0
    for b in data.get("content", []):
        if b.get("type") == "thinking":
            thinking_chars += len(b.get("thinking", ""))
        elif b.get("type") == "text":
            text_chars += len(b.get("text", ""))
    return {
        "mode": "non-stream",
        "wall_ms": (t_end - t_start) * 1000,
        "ttft_any_ms": None,
        "ttft_thinking_ms": None,
        "ttft_text_ms": None,
        "thinking_chars": thinking_chars,
        "text_chars": text_chars,
        "output_tokens": usage.get("output_tokens"),
        "stop_reason": data.get("stop_reason"),
        "usage": usage,
    }


def summarize(r):
    out = r["output_tokens"] or 0
    wall_s = r["wall_ms"] / 1000
    thinking_chars = r["thinking_chars"]
    text_chars = r["text_chars"]
    total_chars = thinking_chars + text_chars or 1
    # usage.output_tokens_details.thinking_tokens is an EXACT split (confirmed
    # present in a real response) -- prefer it; fall back to the char-share
    # proportional estimate only if it's ever absent.
    details = (r.get("usage") or {}).get("output_tokens_details") or {}
    thinking_tokens_exact = details.get("thinking_tokens")
    if thinking_tokens_exact is not None:
        visible_tokens_est = out - thinking_tokens_exact
        visible_frac = (visible_tokens_est / out) if out else None
    else:
        visible_frac = text_chars / total_chars
        visible_tokens_est = out * visible_frac
    total_tps = out / wall_s if wall_s else None

    lines = [
        f"=== {r['mode']} ===",
        f"wall_s={wall_s:.1f} output_tokens(total, incl. thinking)={out} "
        f"thinking_tokens_exact={thinking_tokens_exact}",
        f"thinking_chars={thinking_chars} text_chars={text_chars} "
        f"(visible_frac={visible_frac:.1%}, visible_tokens_est~{visible_tokens_est:.0f})"
        if visible_frac is not None
        else "(no output tokens)",
        f"total_tok_s(all output/wall)={total_tps:.1f}"
        if total_tps
        else "total_tok_s=NA",
    ]
    if r["ttft_any_ms"] is not None:
        think_str = (
            f"{r['ttft_thinking_ms']:.0f}"
            if r["ttft_thinking_ms"] is not None
            else "N/A(redacted/none)"
        )
        lines.append(
            f"ttft_any_ms={r['ttft_any_ms']:.0f} ttft_thinking_ms={think_str} ttft_text_ms={r['ttft_text_ms']:.0f}"
        )
        # Valid even when thinking produced no visible deltas (redacted) --
        # ttft_text_ms alone marks when the visible-output phase began.
        if r["ttft_text_ms"]:
            pre_text_s = r["ttft_text_ms"] / 1000
            post_text_s = wall_s - pre_text_s
            visible_tps_phase = (
                (visible_tokens_est / post_text_s) if post_text_s > 0 else None
            )
            lines.append(
                f"time before first visible text token: {pre_text_s:.1f}s "
                f"(prefill+thinking phase) -- decode rate DURING the visible-text phase only: "
                f"{visible_tps_phase:.1f} tok/s"
                if visible_tps_phase
                else "n/a"
            )
    cache_read = (r.get("usage") or {}).get("cache_read_input_tokens")
    cache_create = (r.get("usage") or {}).get("cache_creation_input_tokens")
    service_tier = (r.get("usage") or {}).get("service_tier")
    speed = (r.get("usage") or {}).get("speed")
    lines.append(
        f"cache_read={cache_read} cache_create={cache_create} "
        f"service_tier={service_tier} speed={speed} stop_reason={r['stop_reason']}"
    )
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["stream", "nonstream"], default="stream")
    p.add_argument(
        "--prompt",
        default=(
            "List exactly 60 distinct, real, verifiable facts about deep-sea "
            "creatures. One fact per line. No headers, no numbering, no commentary."
        ),
    )
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument(
        "--thinking",
        choices=["enabled", "adaptive", "disabled"],
        default="adaptive",
        help="thinking.type -- 'adaptive' (the default) is the newer mode "
        "paired with --effort, matches docs/THROUGHPUT_RESEARCH.md's "
        "Recommended Configuration, and is very likely what Claude Code "
        "itself sends on Sonnet-5-class models; 'enabled' is the legacy "
        "manual mode (needs --thinking-budget); 'disabled' omits the "
        "thinking block entirely. (Default was 'enabled' until 2026-07-17 "
        "-- changed via external peer review, which flagged the previous "
        "default as silently contradicting this project's own recommendation.)",
    )
    p.add_argument("--thinking-budget", type=int, default=8000)
    p.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        default=None,
        help="output_config.effort -- Claude Code's own --effort flag maps here",
    )
    p.add_argument(
        "--speed",
        choices=["fast"],
        default=None,
        help="top-level speed param (Fast Mode)",
    )
    p.add_argument("--service-tier", choices=["auto", "standard_only"], default=None)
    p.add_argument("--inference-geo", default=None)
    p.add_argument(
        "--extra-beta",
        action="append",
        default=[],
        help="append an extra anthropic-beta value (repeatable)",
    )
    p.add_argument(
        "--cache-ttl",
        default="1h",
        help="cache_control ttl on the shared prefix's last block ('1h', '5m', or 'none' to disable)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.cache_ttl == "none":
        args.cache_ttl = None
    messages = build_messages(args.prompt, args.cache_ttl)
    if args.mode == "stream":
        result = run_streaming(messages, args)
    else:
        result = run_nonstreaming(messages, args)
    print(summarize(result))
    print(json.dumps(result, indent=2), file=sys.stderr)
