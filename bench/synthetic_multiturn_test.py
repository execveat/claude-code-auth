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

Tests whether extended-thinking token proportion (highly variable, confirmed
via usage.output_tokens_details.thinking_tokens) and streaming-vs-non-
streaming interact with real, realistic (long, cached) context -- not just
clean synthetic single-turn benchmarks. Extended thinking is ENABLED with a
real budget so we can separate TOTAL output tokens (thinking + visible text,
what usage.output_tokens actually reports) from VISIBLE-ONLY output tokens
(text blocks only -- what a person watching the terminal actually sees).
Reports both tok/s figures plus a time breakdown: time-to-first-thinking-
token vs time-to-first-VISIBLE-text-token, so we can see exactly how much
wall-clock is invisible thinking before any visible output starts.

Usage:
    uv run python bench/synthetic_multiturn_test.py stream "<prompt>"
    uv run python bench/synthetic_multiturn_test.py nonstream "<prompt>"

Requires bench/fixtures/synthetic_history.json to exist -- see bench/README.md
to build it (gitignored; not checked in, since it's a verbatim slice of a
real conversation).
"""

import json
import sys
import time
from pathlib import Path

import requests

from claude_code_auth import ClaudeCodeOAuthManager

URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"
REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = REPO_ROOT / "bench" / "fixtures" / "synthetic_history.json"
THINKING_BUDGET = 8000
MAX_TOKENS = 20000


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


def build_messages(trailing_prompt):
    turns = load_shared_prefix()
    last_block = turns[-1]["content"][-1]
    last_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    turns.append(
        {"role": "user", "content": [{"type": "text", "text": trailing_prompt}]}
    )
    return turns


def build_body(messages, stream):
    # Lower refresh margin: claude-code-auth's proactive refresh (default
    # 30-min-out margin) currently 404s against the token endpoint (a
    # pre-existing, separate bug -- tracked, not fixed here). The access
    # token itself is still valid; just don't trigger the broken refresh
    # path for a token that isn't actually expired yet.
    manager = ClaudeCodeOAuthManager(refresh_margin_ms=60_000)
    text = first_user_message_text(messages)
    system = manager.build_system_blocks(text)
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "stream": stream,
        "system": system,
        "thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET},
        "messages": messages,
    }, manager.build_headers()


def run_streaming(messages):
    body, headers = build_body(messages, stream=True)
    t_start = time.time()
    t_first_any = None
    t_first_thinking = None
    t_first_text = None
    block_types = {}  # index -> type
    thinking_chars = 0
    text_chars = 0
    output_tokens = None
    stop_reason = None
    usage_final = None

    with requests.post(
        URL, headers=headers, json=body, stream=True, timeout=300
    ) as resp:
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

            if etype == "content_block_start":
                idx = evt["index"]
                block_types[idx] = evt["content_block"]["type"]
            elif etype == "content_block_delta":
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
                usage_final = evt.get("usage") or usage_final
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


def run_nonstreaming(messages):
    body, headers = build_body(messages, stream=False)
    t_start = time.time()
    resp = requests.post(URL, headers=headers, json=body, timeout=300)
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
    lines.append(
        f"cache_read={cache_read} cache_create={cache_create} stop_reason={r['stop_reason']}"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stream"
    prompt = (
        sys.argv[2]
        if len(sys.argv) > 2
        else "List exactly 60 distinct, real, verifiable facts about deep-sea creatures. One fact per line. No headers, no numbering, no commentary."
    )
    messages = build_messages(prompt)
    if mode == "stream":
        result = run_streaming(messages)
    elif mode == "nonstream":
        result = run_nonstreaming(messages)
    else:
        raise SystemExit(f"unknown mode {mode!r}")
    print(summarize(result))
    print(json.dumps(result, indent=2), file=sys.stderr)
