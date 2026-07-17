# bench/ — realistic-context throughput benchmarking

Tools for measuring real-world Claude API throughput (tokens/sec) using
`claude-code-auth`'s OAuth-subscription credentials directly against the raw
Messages API — bypassing the Claude Code CLI/harness entirely. This is the
harness behind the findings in `docs/THROUGHPUT_RESEARCH.md`.

## Why "synthetic multi-turn" instead of a clean single-turn prompt

A clean, short, single-turn benchmark measures decode speed in a regime
Claude Code almost never actually runs in: real sessions carry tens to
hundreds of KB of accumulated conversation (tool calls, file contents, prior
turns) as cached context, and extended thinking is enabled by default. Both
of those change the numbers. So instead of inventing a synthetic history, we
**transplant a real window from a real Claude Code session transcript** —
preserving genuine tool_use/tool_result structure, real message sizes, and
real turn-taking patterns — then send it as a single large request with one
new trailing prompt appended, cache-marked so repeated trials only pay for
the one new prompt.

## Building the fixture

```bash
uv run python bench/build_synthetic_history.py <session.jsonl> <target_chars> [out.json]
```

- `<session.jsonl>` — any real Claude Code session transcript, e.g. one under
  `~/.config/ccs/profiles/<profile>/projects/<project-dir>/<session-id>.jsonl`.
  Pick a long, tool-call-heavy session for a realistic mix of text/tool_use/
  tool_result blocks.
- `<target_chars>` — approximate character budget for the window (rough proxy
  for token count; `chars / 3.3` approximates tokens for typical
  English+code mixes). E.g. `660000` for a ~200K-token window.
- `[out.json]` — defaults to `bench/fixtures/synthetic_history.json`.

The script normalizes the transcript (merges multi-block assistant messages,
drops thinking/redacted_thinking blocks from history, repairs occasional
tool_use/tool_result mis-ordering — see the module docstring for the exact
quirks handled) and writes a structurally valid `messages` array ready to
send to the API.

**`bench/fixtures/*` is gitignored** — it's a verbatim slice of a real
conversation transcript (potentially containing real file contents, paths,
or other session-specific detail), not project source, and it's cheap to
regenerate from any transcript on hand. Build your own fixture locally
before running the benchmark; don't expect one to already exist after a
fresh clone.

## Running a single trial

```bash
uv run python bench/synthetic_multiturn_test.py --mode stream --prompt "<trailing prompt>"
uv run python bench/synthetic_multiturn_test.py --mode nonstream --thinking adaptive --effort high
uv run python bench/synthetic_multiturn_test.py --mode stream --thinking enabled --thinking-budget 8000
uv run python bench/synthetic_multiturn_test.py --mode stream --thinking disabled --effort low
uv run python bench/synthetic_multiturn_test.py --model claude-opus-4-8 --extra-beta fast-mode-2026-02-01 --speed fast
```

Full flag reference: `--mode {stream,nonstream}`, `--prompt`, `--model`,
`--max-tokens`, `--thinking {enabled,adaptive,disabled}` (`enabled` is the
legacy manual mode and needs `--thinking-budget`; `adaptive` is the newer
mode, pair it with `--effort`; `disabled` omits the thinking block
entirely), `--thinking-budget`, `--effort {low,medium,high,xhigh,max}`
(`output_config.effort` — Claude Code's own `--effort` flag maps here),
`--speed fast` (Fast Mode — model- and billing-gated, see
`docs/THROUGHPUT_RESEARCH.md` F3), `--service-tier {auto,standard_only}`,
`--inference-geo`, `--extra-beta` (repeatable, appends an extra
`anthropic-beta` value), `--cache-ttl` (`1h`, `5m`, or `none` to disable
`cache_control` on the shared prefix entirely).

Vary only the trailing prompt (and whichever single lever you're testing)
between trials — never the shared prefix itself — so each trial benefits
from prompt caching (only the new prompt + response is paid for at full
price; the shared history is a cache read after the first call, or a cache
hit within the TTL of any earlier call in this process lineage).

Each run prints a summary: total wall time, `output_tokens` (thinking +
visible), the **exact** thinking/visible split via
`usage.output_tokens_details.thinking_tokens`, tok/s figures (both blended
and visible-phase-only when streaming), and cache read/write token counts.
The raw JSON result is also printed to stderr for programmatic use.

## Running a sweep (multiple conditions, many trials)

`bench/run_sweep.py` drives `synthetic_multiturn_test.py` across a set of
named conditions, SERIALLY (one real API call at a time, never concurrent —
see "Why sweeps run serially" below), and writes structured results to a
JSONL file for later aggregation.

A sweep spec is a small JSON file:

```json
{
  "prompt": "List exactly 20 distinct, real, verifiable facts about deep-sea creatures. One fact per line. No headers, no numbering, no commentary.",
  "thinking": "adaptive",
  "max_tokens": 4000,
  "conditions": [
    {"label": "effort-low", "args": {"effort": "low"}},
    {"label": "effort-high", "args": {"effort": "high"}}
  ]
}
```

Top-level keys other than `conditions` are defaults shared by every
condition; each condition's own `args` overrides them. Any field
`synthetic_multiturn_test.py`'s CLI accepts (`mode`, `prompt`, `model`,
`max_tokens`, `thinking`, `thinking_budget`, `effort`, `speed`,
`service_tier`, `inference_geo`, `extra_beta`, `cache_ttl`) can appear at
either level. See `bench/specs/example_effort_sweep.json` for a working example.

```bash
# Always dry-run first -- prints the full resolved trial plan, makes ZERO network calls.
uv run python bench/run_sweep.py --spec bench/specs/example_effort_sweep.json --dry-run --trials-per-condition 2

# Then run for real:
uv run python bench/run_sweep.py --spec bench/specs/example_effort_sweep.json \
    --trials-per-condition 3 --delay-seconds 5 --out bench/results/effort_sweep.jsonl
```

`bench/results/*.jsonl` is gitignored — it's real usage data (real prompts,
timings, token counts), not source.

### Why sweeps run serially

Several experiments in `docs/THROUGHPUT_RESEARCH.md`'s Open Questions are
single-lever A/Bs (effort, caching, redact-thinking, structured outputs,
etc.) that assume no OTHER live traffic is hitting the account at the same
time — and the concurrency question itself (does parallel load change
per-request tok/s?) is one of the things under investigation. Running
multiple experiments' real API calls concurrently would contaminate every
other experiment's numbers with an uncontrolled concurrency effect.
`run_sweep.py` therefore always executes one trial at a time, with a
configurable `--delay-seconds` pause between them; don't launch two
`run_sweep.py` invocations against the live API concurrently for this same
reason. The dedicated concurrency experiment gets its own isolated run with
nothing else calling the API at the same time.

## Analyzing sweep results

```bash
uv run python bench/analyze_results.py bench/results/effort_sweep.jsonl
```

Groups trials by `label` and prints, per group: n, median + IQR of wall
time, median + IQR of blended total tok/s (`output_tokens / wall_s`), median
+ IQR of the thinking-token fraction (exact via
`usage.output_tokens_details.thinking_tokens` when present), and — for
streaming trials only — median + IQR of visible-phase-only tok/s (decode
rate during the visible-text phase, after the first visible text token
arrives). The per-trial math mirrors `synthetic_multiturn_test.py`'s own
`summarize()` function exactly, just returning numbers instead of a
formatted string so it can be aggregated across trials.

## Known caveats

- The OAuth attribution/fingerprint system block (`manager.build_system_blocks`)
  is mandatory — omitting it produces a bare 429 that looks like a quota
  issue but isn't.
