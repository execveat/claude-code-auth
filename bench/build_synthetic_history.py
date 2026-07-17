#!/usr/bin/env python3
"""Reconstruct a valid, API-replayable slice of conversation history from a
real Claude Code session transcript, for the synthetic multi-turn realistic
context throughput test (see synthetic_multiturn_test.py in this directory).

Algorithm:
- Only main-thread records (isSidechain == False) -- sidechain records belong
  to a nested sub-agent (Task/Agent tool) conversation and would corrupt
  linear turn structure if mixed in.
- Assistant messages are logged as ONE JSONL line per content block (thinking,
  text, tool_use, ...), all sharing the same message.id, in sequence --
  confirmed empirically (a single logical assistant turn with a thinking
  block + a text block + 3 tool_use blocks appears as 5 separate JSONL
  records). Reconstruct by concatenating blocks for records sharing the same
  assistant message.id, in file order.
- User messages: merge consecutive user-role records with no intervening
  assistant record into one turn (handles parallel tool_result batches that
  may be logged as separate lines).
- Thinking/redacted_thinking blocks are DROPPED from historical assistant
  turns: their real text is usually redacted anyway (signature-only, empty
  "thinking" field), and Anthropic's API does not require historical turns
  to carry valid thinking signatures -- only an in-flight continuation of an
  unfinished agentic loop does, and we always cut at a clean turn boundary.

Usage:
    uv run python bench/build_synthetic_history.py <session.jsonl> <target_chars> [out.json]

Note: the transcript path and the built fixture JSON are NOT part of this
repo's git history -- real Claude Code session transcripts (the source) and
the built fixture (a verbatim slice of one) both contain real conversation
content, so bench/fixtures/*.json is gitignored. Regenerate on demand; see
bench/README.md for the exact recipe and a known-good source transcript.
"""

import json
import sys


def load_and_normalize(path):
    """Merge ANY consecutive same-role records into one turn, regardless of
    message id. This uniformly covers: (a) one logical assistant message
    split across N lines (same id, confirmed empirically), (b) parallel
    tool_result batches logged as separate user-role lines, and (c) a rare
    (6-in-2010 in one real session) case of two genuinely distinct assistant
    message ids appearing back to back with no user turn between them --
    likely an internal retry/continuation the transcript format doesn't
    fully explain, and not worth chasing: merging preserves every
    tool_use/tool_result id pairing (the only thing that actually matters
    for API validity) and guarantees strict role alternation either way.
    """
    turns = []  # list of {"role": ..., "content": [...]}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("isSidechain"):
                continue
            if rec.get("type") not in ("user", "assistant"):
                continue
            msg = rec.get("message") or {}
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                blocks = [{"type": "text", "text": content}] if content else []
            elif isinstance(content, list):
                blocks = content
            else:
                blocks = []

            if role == "assistant":
                blocks = [
                    b
                    for b in blocks
                    if b.get("type") not in ("thinking", "redacted_thinking")
                ]

            if not blocks:
                continue

            if turns and turns[-1]["role"] == role:
                turns[-1]["content"].extend(blocks)
            else:
                turns.append({"role": role, "content": list(blocks)})

    return turns


def estimate_chars(turn):
    total = 0
    for b in turn["content"]:
        t = b.get("type")
        if t == "text":
            total += len(b.get("text", ""))
        elif t == "tool_use":
            total += len(b.get("name", "")) + len(json.dumps(b.get("input", {})))
        elif t == "tool_result":
            c = b.get("content")
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for cb in c:
                    if isinstance(cb, dict) and cb.get("type") == "text":
                        total += len(cb.get("text", ""))
    return total


def select_window(turns, target_chars):
    """Find the smallest suffix of `turns` that (a) starts at a clean user
    turn (plain text, no tool_result blocks -- a genuine fresh prompt) and
    (b) has at least `target_chars` of estimated content. Clean-boundary
    candidates rarely align with the exact char target, so this scans ALL
    candidates rather than stopping at the first backward-walk landing spot
    (which is very likely to be mid-tool-loop, as the initial naive version
    discovered: it failed 100% of the time at small windows).
    """
    n = len(turns)
    suffix_chars = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_chars[i] = suffix_chars[i + 1] + estimate_chars(turns[i])

    candidates = [
        j
        for j, t in enumerate(turns)
        if t["role"] == "user" and all(b.get("type") == "text" for b in t["content"])
    ]
    if not candidates:
        return None, 0

    qualifying = [j for j in candidates if suffix_chars[j] >= target_chars]
    if qualifying:
        # Smallest suffix_chars among those still >= target == least excess.
        best = min(qualifying, key=lambda j: suffix_chars[j])
    else:
        # Nothing reaches the target; take the earliest (largest) window available.
        best = candidates[0]
    return turns[best:], suffix_chars[best]


def validate_alternation(turns):
    for i in range(1, len(turns)):
        if turns[i]["role"] == turns[i - 1]["role"]:
            return False, i
    if turns and turns[0]["role"] != "user":
        return False, 0
    return True, -1


def repair_tool_pairing(turns):
    """Anthropic's API requires each tool_result to reference a tool_use in
    the IMMEDIATELY PRECEDING message (not just anywhere earlier). Real
    transcript file order occasionally violates this -- confirmed
    empirically: a tool_result for a given id appeared in the JSONL one
    assistant-turn EARLIER than its own tool_use block (likely an artifact
    of Claude Code's own async logging of parallel tool calls, not
    something our reconstruction caused). Fix by keeping, for every
    (assistant, following-user) pair, only the tool_use/tool_result ids
    that appear on BOTH sides -- drops the rare mismatched block instead of
    letting it 400 the whole request.
    """
    dropped = 0
    for i in range(len(turns) - 1):
        if turns[i]["role"] != "assistant" or turns[i + 1]["role"] != "user":
            continue
        assistant_ids = {
            b["id"] for b in turns[i]["content"] if b.get("type") == "tool_use"
        }
        user_turn = turns[i + 1]
        result_ids = {
            b["tool_use_id"]
            for b in user_turn["content"]
            if b.get("type") == "tool_result"
        }
        valid = assistant_ids & result_ids
        before_a = len(turns[i]["content"])
        turns[i]["content"] = [
            b
            for b in turns[i]["content"]
            if b.get("type") != "tool_use" or b["id"] in valid
        ]
        dropped += before_a - len(turns[i]["content"])
        before_u = len(user_turn["content"])
        user_turn["content"] = [
            b
            for b in user_turn["content"]
            if b.get("type") != "tool_result" or b["tool_use_id"] in valid
        ]
        dropped += before_u - len(user_turn["content"])

    non_empty = [t for t in turns if t["content"]]
    # Dropping an emptied turn can leave two same-role turns adjacent (e.g. a
    # user turn between two assistant turns vanishes) -- re-merge before
    # returning so alternation still holds.
    merged = []
    for t in non_empty:
        if merged and merged[-1]["role"] == t["role"]:
            merged[-1]["content"].extend(t["content"])
        else:
            merged.append(t)
    return merged, dropped


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "usage: build_synthetic_history.py <session.jsonl> <target_chars> [out.json]",
            file=sys.stderr,
        )
        sys.exit(1)

    path = sys.argv[1]
    target_chars = int(sys.argv[2])
    out_path = (
        sys.argv[3] if len(sys.argv) > 3 else "bench/fixtures/synthetic_history.json"
    )

    all_turns = load_and_normalize(path)
    print(f"total normalized turns: {len(all_turns)}", file=sys.stderr)

    window, chars = select_window(all_turns, target_chars)
    if window is None:
        print("no clean user-turn boundary found in window!", file=sys.stderr)
        sys.exit(1)

    window, dropped = repair_tool_pairing(window)
    print(f"repair_tool_pairing dropped {dropped} mismatched blocks", file=sys.stderr)

    ok, bad_idx = validate_alternation(window)
    print(
        f"window turns: {len(window)}  est_chars: {chars}  alternation_ok: {ok} (bad_idx={bad_idx})",
        file=sys.stderr,
    )
    print(
        f"first turn role: {window[0]['role']}  first block types: {[b.get('type') for b in window[0]['content'][:3]]}",
        file=sys.stderr,
    )
    print(
        f"last turn role: {window[-1]['role']}  last block types: {[b.get('type') for b in window[-1]['content'][:3]]}",
        file=sys.stderr,
    )

    with open(out_path, "w") as f:
        json.dump(window, f)
    print(f"wrote {out_path}", file=sys.stderr)
