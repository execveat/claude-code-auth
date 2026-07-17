#!/usr/bin/env python3
"""Warm-cached-context LENGTH vs decode tok/s (TASK-025, peer-review-surfaced
follow-up to F4).

F4 already established that caching itself (hit vs miss) doesn't affect
decode speed. This asks a different, more subtle question: at a FIXED cache
status (always a warm cache-HIT), does the raw LENGTH of the cached prefix
that precedes generation have any effect on the decode-phase token rate that
follows it? Plausible mechanism: KV-cache size could affect per-decode-step
attention cost independent of whether that KV cache was just written or read
from a server-side cache.

Design: three context-length conditions --
  ctx_min   a small deterministic filler block kept just ABOVE Anthropic's
            practical minimum cacheable-prefix length (~1024 tokens) -- a
            literal 0-token block couldn't be marked cacheable at all, so
            this is the practical floor, not literally zero.
  ctx_50k   bench/fixtures/synthetic_history.json truncated to a contiguous
            prefix (from turn 0) whose cumulative json.dumps-chars/4 crosses
            ~50K tokens.
  ctx_358k  the full, untruncated shared history -- same as every other tool
            in this mission (~358K measured cache tokens per F20/F23).

Per condition: ONE unique uuid4() marker is injected into the cache_control-
marked block's TEXT (same isolation technique as cache_ttl_gap_test.py and
cold_cache_stampede_test.py) so this condition's cache entry never collides
with any other condition or any other bench/ tool's traffic. ONE warmup call
pays the cache-write; then N measurement calls reuse the IDENTICAL messages
(same marker), so every measurement call should land as a clean cache_read --
holding cache status constant across conditions and isolating context LENGTH
as the only varying factor. A measurement call that unexpectedly shows
cache_creation instead of cache_read is flagged loudly (contaminated trial).

Decode-phase tok/s per measurement call mirrors
synthetic_multiturn_test.summarize()'s visible_tps_phase: visible-text
tokens (output_tokens minus the exact thinking_tokens split, though thinking
is disabled here so this is just output_tokens) divided by wall-clock time
AFTER the first visible text token (wall_ms - ttft_text_ms) -- the decode
phase only, excluding prefill/TTFT. Requires --mode stream under the hood
(only run_streaming reports ttft_text_ms), so this tool always streams.

Usage:
    uv run python bench/context_length_decode_test.py --dry-run
    uv run python bench/context_length_decode_test.py --n 3
"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import synthetic_multiturn_test as smt

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "bench" / "results" / "task025_context_length_decode.jsonl"

GENERATION_PROMPT = (
    "List exactly 500 distinct, real, verifiable facts about deep-sea "
    "creatures. One fact per line. No headers, no numbering, no commentary."
)

# Deterministic filler kept comfortably above Anthropic's ~1024-token
# practical minimum cacheable prefix length, so ctx_min still genuinely
# engages caching rather than silently no-op'ing cache_control on
# too-short content. ~120 repeats * ~72 chars ~= 8640 chars ~= ~2100 token
# estimate -- a healthy margin above the floor.
_FILLER_SENTENCE = (
    "The quick brown fox jumps over the lazy dog near the old stone bridge. "
)
MIN_FILLER_TEXT = _FILLER_SENTENCE * 120


def resolve_dangling_tool_use(turns):
    """If truncation cut off right after an assistant turn that issued a
    tool_use call, the real API rejects the request (HTTP 400: "tool_use
    ids were found without tool_result blocks immediately after") since it
    requires the matching tool_result in the VERY NEXT message. Rather than
    searching backward for a "clean" cut point (real Claude Code transcripts
    have tool calls on most assistant turns, so that search can pop nearly
    the entire window), synthesize a trivial placeholder tool_result turn
    for every dangling tool_use id -- this keeps the truncation length
    close to its target budget regardless of where it lands."""
    if not turns:
        return turns
    last = turns[-1]
    if last["role"] != "assistant":
        return turns
    content = last.get("content")
    if not isinstance(content, list):
        return turns
    tool_use_ids = [b.get("id") for b in content if b.get("type") == "tool_use"]
    if tool_use_ids:
        turns.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "ok (placeholder -- context-length-decode truncation)",
                    }
                    for tid in tool_use_ids
                ],
            }
        )
    return turns


def build_min_turns():
    """A minimal, deterministic 2-turn stub -- NOT drawn from the real
    conversation fixture -- kept just above the practical minimum cacheable
    prefix length. This is the ctx_min condition's base (before marker
    injection)."""
    return [
        {"role": "user", "content": [{"type": "text", "text": MIN_FILLER_TEXT}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Acknowledged, filler content noted."}
            ],
        },
    ]


def truncate_to_budget(turns, char_budget):
    """Keep a contiguous prefix (from index 0) of `turns` whose cumulative
    json.dumps size first reaches char_budget -- preserves the original
    user/assistant alternation exactly since we never skip an index."""
    cum = 0
    kept = []
    for t in turns:
        cum += len(json.dumps(t))
        kept.append(t)
        if cum >= char_budget:
            break
    return resolve_dangling_tool_use(kept)


CONDITIONS = {
    "ctx_min": lambda: build_min_turns(),
    "ctx_50k": lambda: truncate_to_budget(smt.load_shared_prefix(), 200_000),
    "ctx_358k": lambda: smt.load_shared_prefix(),
}


def build_marked_messages(condition, marker):
    """Appends a DEDICATED marker+cache_control text block, then the
    trailing generation prompt -- regardless of what role the truncated
    prefix currently ends on. If it ends on 'assistant' (the common case),
    the marker is appended into that turn's own content list (a brand-new
    block, never depending on the pre-existing last block's type/text --
    real transcript turns can end on a tool_use/tool_result block instead
    of plain text, so mutating that block would be fragile). If it ends on
    'user' instead, the marker becomes its own new 'assistant' turn so
    alternation stays valid before the final 'user' generation turn."""
    turns = CONDITIONS[condition]()
    marker_block = {
        "type": "text",
        "text": f"[context-length-decode marker: {marker}]",
        "cache_control": {"type": "ephemeral", "ttl": "5m"},
    }
    if turns and turns[-1]["role"] == "assistant":
        last_turn = turns[-1]
        if isinstance(last_turn["content"], str):
            last_turn["content"] = [{"type": "text", "text": last_turn["content"]}]
        last_turn["content"].append(marker_block)
    else:
        turns.append({"role": "assistant", "content": [marker_block]})
    turns.append(
        {"role": "user", "content": [{"type": "text", "text": GENERATION_PROMPT}]}
    )
    return turns


def build_args(max_tokens):
    return SimpleNamespace(
        mode="stream",
        model=smt.DEFAULT_MODEL,
        max_tokens=max_tokens,
        thinking="disabled",
        thinking_budget=8000,
        thinking_display=None,
        effort=None,
        speed=None,
        service_tier=None,
        inference_geo=None,
        output_schema=None,
        extra_beta=[],
        cache_ttl=None,  # unused -- we build messages ourselves via build_marked_messages
    )


def one_call(messages, args):
    t0 = time.time()
    result = smt.run_streaming(messages, args)
    t1 = time.time()
    return {"call_start": t0, "call_end": t1, "result": result}


def decode_tok_s(result):
    """TOTAL-output decode rate: all output tokens (thinking + visible --
    both are generated by the same autoregressive decode loop at the same
    per-step cost, see F4) divided by wall-clock time AFTER the first
    content delta of ANY kind (ttft_any_ms), i.e. the decode phase only,
    excluding prefill/TTFT. Deliberately NOT restricted to visible-text-only
    (synthetic_multiturn_test.summarize()'s visible_tps_phase): this
    experiment found `thinking="disabled"` (omitting the `thinking` field
    entirely) does NOT reliably suppress server-side thinking -- some
    responses spent their ENTIRE max_tokens budget on invisible reasoning
    with zero visible text ever starting, which would make a visible-only
    metric undefined for those trials. Total-output/ttft_any is robust to
    that and is the metric this experiment actually needs (raw decode-loop
    throughput, independent of the thinking/visible split -- exactly the
    confound F4 already isolated as orthogonal)."""
    out = result["output_tokens"] or 0
    ttft_any_ms = result.get("ttft_any_ms")
    if not ttft_any_ms or not out:
        return None
    post_prefill_s = (result["wall_ms"] - ttft_any_ms) / 1000
    if post_prefill_s <= 0:
        return None
    return out / post_prefill_s


def usage_row(call_record):
    result = call_record["result"]
    u = result.get("usage") or {}
    details = u.get("output_tokens_details") or {}
    return {
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": u.get("cache_read_input_tokens"),
        "input_tokens": u.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "thinking_tokens": details.get("thinking_tokens"),
        "wall_ms": result["wall_ms"],
        "ttft_any_ms": result.get("ttft_any_ms"),
        "ttft_text_ms": result.get("ttft_text_ms"),
        "stop_reason": result.get("stop_reason"),
        "decode_tok_s": decode_tok_s(result),
    }


def run_condition(condition, n, args, out_f):
    marker = str(uuid.uuid4())
    print(f"[{condition}] marker={marker[:8]}... warmup call...", file=sys.stderr)
    warmup_messages = build_marked_messages(condition, marker)
    warmup_args = build_args(max_tokens=100)
    warmup = one_call(warmup_messages, warmup_args)
    wu = usage_row(warmup)
    print(
        f"[{condition}] warmup: cache_create={wu['cache_creation_input_tokens']} "
        f"cache_read={wu['cache_read_input_tokens']} input_tokens={wu['input_tokens']}",
        file=sys.stderr,
    )

    measurement_rows = []
    for i in range(n):
        messages = build_marked_messages(condition, marker)
        r = one_call(messages, args)
        row = usage_row(r)
        row["trial"] = i
        if row["cache_creation_input_tokens"]:
            print(
                f"[{condition}] trial={i} WARNING: measurement call shows "
                f"cache_creation={row['cache_creation_input_tokens']} (expected 0 -- "
                f"cache status not held constant, this trial is CONTAMINATED)",
                file=sys.stderr,
            )
        print(
            f"[{condition}] trial={i} cache_read={row['cache_read_input_tokens']} "
            f"decode_tok_s={row['decode_tok_s']}",
            file=sys.stderr,
        )
        measurement_rows.append(row)

    record = {
        "experiment": "task025_context_length_decode",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "condition": condition,
        "marker": marker,
        "warmup": wu,
        "measurements": measurement_rows,
    }
    out_f.write(json.dumps(record) + "\n")
    out_f.flush()
    return record


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n", type=int, default=3, help="measurement calls per condition")
    p.add_argument("--max-tokens", type=int, default=3000)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned conditions/trials and exit -- zero network calls",
    )
    return p.parse_args()


def main():
    cli = parse_args()
    conditions = list(CONDITIONS.keys())

    if cli.dry_run:
        print(f"planned conditions: {conditions}")
        print(f"per condition: 1 warmup call + {cli.n} measurement calls")
        print(f"total calls: {len(conditions) * (cli.n + 1)}")
        for c in conditions:
            turns = CONDITIONS[c]()
            approx_chars = sum(len(json.dumps(t)) for t in turns)
            print(
                f"  {c}: {len(turns)} turns, ~{approx_chars} chars "
                f"(~{approx_chars // 4} tok estimate)"
            )
        print("zero network calls made (--dry-run).")
        return

    args = build_args(cli.max_tokens)
    out_path = Path(cli.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    records = []
    with open(out_path, "a") as f:
        for condition in conditions:
            record = run_condition(condition, cli.n, args, f)
            records.append(record)

    print(f"\nwrote results to {out_path}", file=sys.stderr)
    print("\n=== SUMMARY ===", file=sys.stderr)
    for r in records:
        rates = [m["decode_tok_s"] for m in r["measurements"] if m["decode_tok_s"]]
        if rates:
            rates_sorted = sorted(rates)
            median = rates_sorted[len(rates_sorted) // 2]
            print(
                f"{r['condition']}: n={len(rates)} decode_tok_s median={median:.1f} "
                f"range=[{min(rates):.1f}, {max(rates):.1f}]",
                file=sys.stderr,
            )
        else:
            print(
                f"{r['condition']}: no valid decode_tok_s measurements", file=sys.stderr
            )


if __name__ == "__main__":
    main()
