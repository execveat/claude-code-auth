# Claude API Throughput Research — evergreen findings

**Status**: living document, autonomous research pass started 2026-07-17 ~01:15 local.
**Owner**: Andrew's Claude Code session (autopilot mode) + external peer review.
**Scope**: what actually moves real-world tokens/sec for Claude Sonnet-5-class
models accessed via OAuth subscription auth (not API-key billing), and how to
get the best of it conveniently from `claude-code-auth`.

This doc is **self-healing**: a finding that gets contradicted by later
evidence moves to "Rejected / Superseded" with a pointer to what replaced it —
it is never silently deleted or left contradicting a newer entry. Every claim
in "Confirmed Findings" cites the evidence that proves it (a script, a set of
raw numbers, a date). A claim without evidence belongs in "Open Questions",
never in "Confirmed".

## How to read this doc

- **Confirmed Findings** — reproducible, evidenced, dated. Safe to build on.
- **Rejected / Superseded Hypotheses** — things that seemed plausible and were
  tested and refuted, or were true once and later superseded. Kept visible so
  nobody re-investigates a dead end.
- **Open Questions** — identified but not yet tested, or tested with
  inconclusive/contradictory results. Each has a proposed disproof/test.
- **Recommended Configuration** — the actionable "best known setup", updated
  as findings solidify. This is what `claude_code_auth`'s helpers implement.
- **Changelog** — dated entries, newest first, one line per material update.

---

## Executive Summary

*(updated as the research lands — placeholder during active investigation)*

Prior session establishe two results before this autonomous pass began:
1. ProtonVPN exit-country and direct-vs-proxied routing have **no measurable
   effect** on Claude throughput (~30 CLI trials, 60-83 tok/s band throughout,
   all conditions overlap).
2. Extended-thinking token proportion is a **major, independent, and highly
   variable** driver of perceived slowness: a real synthetic-multi-turn test
   (358K-token cached context) showed one call spending 83% of its total
   output on invisible thinking tokens (`usage.output_tokens_details.thinking_tokens`),
   making the *visible* text arrive at ~18 tok/s even though total decode
   throughput was a healthy 103.5 tok/s. This is separate from, and additive
   to, plain fixed-per-request-latency overhead (short total-output turns pay
   the same ~2s TTFT as long ones, cited in a 254-turn historical bucketing:
   out<300 tokens -> median 43 tok/s, out>=3000 -> median 86 tok/s).

## Confirmed Findings

*(none yet added by this autonomous pass — filled in as experiments land)*

## Rejected / Superseded Hypotheses

- **VPN exit-country geo-mirroring** — hypothesized that Anthropic might route
  inference to region-local compute. Refuted: Anthropic's global endpoint
  "dynamically routes requests to regions with available capacity," decided
  server-side, not client-IP-keyed; ~30 trials across NL/US/KR/direct showed
  no separable effect. (Prior session, `plans/concurrent-napping-wren.md`.)
- **Claude Code CLI's `ttft_ms` anomaly reflects a real network/server issue**
  — refuted: 6 raw wire-level calls (bypassing the CLI) never showed the
  anomaly; it's a CLI-side instrumentation bug. (Prior session.)

## Open Questions

*(populated by the research wave — each gets a proposed cheapest disproof test)*

## Recommended Configuration

*(placeholder — populated once experiments confirm specific levers)*

## Methodology Notes

- Raw-API testing uses `~/Projects/cc/claude-code-auth` (OAuth credential
  sharing with the real Claude Code subscription — no separate API key/billing).
- **The OAuth attribution/fingerprint system block is mandatory** for any raw
  call to succeed — see `src/claude_code_auth/fingerprint.py`. Omitting it
  produces a bare 429 with no rate-limit headers, which looks like a quota
  problem but isn't.
- Every experiment that isn't a one-line config toggle should be reproducible
  from a committed script under `~/Projects/cc/claude-code-auth/bench/` (or
  wherever the integration wave lands the benchmarking tooling) — not a
  one-off `/tmp` script that evaporates.
- Real-money real-API calls: keep a running approximate cost tally in this
  doc's changelog so the scope of spend stays visible.

## Changelog

- 2026-07-17: Doc created; autonomous 8h research+build pass kicked off
  (autopilot + parallel-orchestration skills). Prior session's two headline
  results (VPN null result, thinking-token-proportion finding) carried
  forward as the starting evidence base.
