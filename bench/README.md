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

**`bench/fixtures/*.json` is gitignored** — it's a verbatim slice of a real
conversation transcript (potentially containing real file contents, paths,
or other session-specific detail), not project source, and it's cheap to
regenerate from any transcript on hand. Build your own fixture locally
before running the benchmark; don't expect one to already exist after a
fresh clone.

## Running a trial

```bash
uv run python bench/synthetic_multiturn_test.py stream "<trailing prompt>"
uv run python bench/synthetic_multiturn_test.py nonstream "<trailing prompt>"
```

Vary only the trailing prompt between trials — never the shared prefix — so
each trial benefits from prompt caching (only the new prompt + response is
paid for at full price; the shared history is a cache read after the first
call, or a cache hit within the TTL of any earlier call in this process
lineage).

Each run prints a summary: total wall time, `output_tokens` (thinking +
visible), the **exact** thinking/visible split via
`usage.output_tokens_details.thinking_tokens`, tok/s figures (both blended
and visible-phase-only when streaming), and cache read/write token counts.
The raw JSON result is also printed to stderr for programmatic use.

## Known caveats

- `ClaudeCodeOAuthManager(refresh_margin_ms=60_000)` is used here to work
  around a real, separate, tracked bug: the library's default 30-minute
  proactive-refresh margin currently 404s against the token endpoint even
  for a still-valid token. See `docs/THROUGHPUT_RESEARCH.md` and the
  `claude-code-auth` planq backlog for the fix.
- The OAuth attribution/fingerprint system block (`manager.build_system_blocks`)
  is mandatory — omitting it produces a bare 429 that looks like a quota
  issue but isn't.
