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

Two headline results carried forward from before this autonomous pass:
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
   the same ~2s TTFT as long ones).

**New this pass (2026-07-17, wave 1 research + coordinator empirical checks):**
3. **`output_config.effort` is a real, documented, GA request field** —
   `low|medium|high|xhigh|max` — that is Claude Code's own `--effort` flag,
   confirmed both in official docs and in `cc-xray` source. This is the
   single most direct, intentional lever for controlling a request's
   compute/thinking depth and therefore its tok/s profile.
4. **A real contradiction between Anthropic's documentation and our own
   empirical OAuth traffic was found and resolved by direct testing, not by
   trusting either source blindly**: docs state manual
   `thinking: {"type":"enabled","budget_tokens":N}` returns HTTP 400 on
   Claude Sonnet 5 (and Opus 4.7/4.8) in favor of the newer
   `thinking: {"type":"adaptive"}` + `effort` combination. Two direct raw-API
   calls this session (both OAuth-authenticated, both against
   `model: "claude-sonnet-5"`) show **both modes return HTTP 200** — the
   documented restriction does not (yet, or not for OAuth callers) apply to
   our actual traffic. See Confirmed Findings for the exact evidence.
5. **"Fast Mode" is real, OAuth-reachable in principle, and gated behind a
   billing feature Andrew's account does not currently have enabled** — a
   direct probe (`speed: "fast"` + `anthropic-beta: fast-mode-2026-02-01` on
   `claude-opus-4-8`) returned a clean, informative `HTTP 429: "Usage credits
   are required for fast mode."` This is the single highest-value **unresolved,
   actionable** lever this mission found: official docs claim up to **2.5x
   higher output tokens/sec** from Fast Mode on Opus 4.7/4.8, at 2x-6x premium
   pricing — but enabling it means turning on pay-as-you-go/overage billing on
   Andrew's account, which is a real billing decision, not something this
   session will make unilaterally. Flagged clearly in Recommended
   Configuration for Andrew's own call when he's back.
6. **Prompt caching has no effect on output/decode throughput at all** — per
   official docs, verbatim: "no effect on output token generation... response
   is identical to what you would get if prompt caching were not used."
   Caching only helps input/prefill (TTFT) and cost. This closes a possible
   conflation risk: our synthetic-multiturn test USES caching (for cost
   reasons, correctly), but caching itself is not what's driving any tok/s
   number we've measured.
7. **Service tiers / Priority Tier are definitively NOT available to
   OAuth/subscription callers** — Priority Tier capacity commitments require
   an organization billing entity and are (as of this investigation) closed
   to new purchases entirely; `claude-code-auth`'s OAuth credentials have no
   such entity. `usage.service_tier` has been `"standard"` in 100% of trials
   with zero variance, consistent with this. This question is closed.
8. **`output_config.effort` is real and substantial, not a no-op — and it
   changes more than thinking depth.** A 5-condition, 18-trial sweep
   (low/medium/high/xhigh/max, held-fixed prompt) shows thinking-token
   fraction rising from ~2% (low/medium) to ~82% (max) and wall-clock rising
   from ~15s to ~65s median for the *identical* prompt. Surprisingly, raw
   decode throughput (total tok/s) *increases* with effort rather than
   degrading (~69-76 tok/s at low/medium/high/xhigh vs ~100 tok/s at max),
   most likely because a longer, largely-uninterrupted generation amortizes
   fixed per-request overhead better than a short one — confirmed directly
   (F12): thinking tokens decode ~1.8-1.9x SLOWER than visible text, so
   overhead dilution has to overcome an opposing effect to produce the
   observed rise. A real operational consideration surfaced too:
   `effort:"max"` needs generous `max_tokens` headroom or it truncates
   mid-thought (reported via `stop_reason`, not silent, but easy to miss).
   See F10, F12.
9. **The mission's ORIGINAL question — streaming vs non-streaming — remains
   OPEN**, corrected after external peer review. An initial attempt (F11)
   claimed streaming was modestly faster, but two independent external
   models (different vendors) found the comparison confounded by an ~11%
   output-length difference between conditions — verified directly against
   the raw data and with an independent permutation test (p≈0.33, not
   significant at n=6/mode). Streaming's one clearly-established advantage
   is observability (a real, measurable time-to-first-visible-text-token),
   not throughput. A properly controlled re-test is filed as TASK-021. See
   F11, F12.
10. **Concurrency is this mission's single most actionable lever, and it's
    free.** N simultaneous calls (same account, same-process thread
    concurrency) show NO detectable per-request throughput degradation up
    to N=16 — every individual call's tok/s stayed in the same ~56-74 band
    as a solo call, zero errors, zero rate-limiting. Aggregate throughput
    scaled ~13.4x at N=16. Unlike Fast Mode (needs a billing change,
    unverified for us) or any single-request tuning, this needs zero
    account changes and works today. See F13.

## Confirmed Findings

### F1. `output_config.effort` — the real request-level effort field (2026-07-17)
Exact wire shape: `{"output_config": {"effort": "high"}}` (nested, NOT a bare
top-level `effort` field). Values: `low`, `medium`, `high`, `xhigh`, `max`.
`xhigh` is only supported on newer models (Sonnet 5, Opus 4.7/4.8, Fable 5,
Mythos 5) — Opus 4.6/Sonnet 4.6/Opus 4.5 top out at `max` without `xhigh`.
`high` is the documented default and is defined as byte-identical to omitting
the field entirely. This is Claude Code's own `--effort` flag
(`resolveAppliedEffort()` in `cc-xray`), and works with or without extended
thinking enabled — it's a behavioral signal affecting ALL output (text, tool
calls, thinking depth), not purely a thinking-budget knob.
Citations: platform.claude.com/docs/en/build-with-claude/effort;
`cc-xray/src/services/api/claude.ts:437-465` (`configureEffortParams`);
`cc-xray/src/utils/effort.ts:13-18`. There is also an Anthropic-internal-only
numeric override path (`anthropic_internal.effort_override`, gated to
`USER_TYPE==='ant'`) — confirmed to exist in source but **not reachable by
external/OAuth callers**, irrelevant to this mission beyond being a documented
dead end.

### F2. Manual `thinking.enabled` mode does NOT 400 on Sonnet 5 via OAuth — empirically confirmed, contradicts docs (2026-07-17)
Anthropic's adaptive-thinking docs page states that `thinking: {"type":
"enabled", "budget_tokens": N}` is rejected with HTTP 400 on Claude Opus
4.8/4.7 and Sonnet 5, which are supposed to require `thinking: {"type":
"adaptive"}` (paired with `effort`) instead. **We tested this directly rather
than trusting the docs or the earlier (docs-derived) claim at face value**:
  - `uv run python bench/synthetic_multiturn_test.py --mode nonstream --thinking enabled --thinking-budget 4000 --max-tokens 500 --prompt "Reply with exactly the word OK and nothing else."` against `model: "claude-sonnet-5"` → **HTTP 200**, `stop_reason: "end_turn"`, real usage object returned (`cache_creation_input_tokens: 358167`, i.e. a genuine full request, not a cached no-op).
  - `uv run python bench/synthetic_multiturn_test.py --mode nonstream --thinking adaptive --effort high --max-tokens 500 --cache-ttl none --prompt "Reply with exactly the word OK and nothing else."` against the same model → **also HTTP 200**.
  - Both modes work. The documented restriction either (a) hasn't been rolled
    out to enforcement yet, (b) doesn't apply to OAuth/subscription-authenticated
    traffic the way it applies to API-key traffic, or (c) is real but
    conditioned on something these two minimal probes didn't trigger (e.g. a
    larger `budget_tokens`, a different max_tokens ratio). **Not fully
    resolved** — this is a confirmed empirical fact (both modes return 200
    for us, today), not a claim about *why* the docs disagree. Don't build
    downstream logic that assumes manual mode is unusable on Sonnet 5;
    equally, don't assume this holds at every budget_tokens value or holds
    indefinitely — re-verify if Anthropic's enforcement changes.
  - Practical implication: our EXISTING synthetic-multiturn results (the 4
    trials from before this pass, using `thinking.enabled` + `budget_tokens:
    8000`) are valid, real, HTTP-200 data — not invalidated by the docs claim.
    But going forward, prefer `adaptive` + `effort` for new experiments where
    possible, since that's very likely what Claude Code's own CLI actually
    sends on Sonnet 5 and later models, making results more representative of
    real usage.

### F3. Fast Mode (`speed: "fast"`) is real, model-gated, and billing-gated (2026-07-17)
Wire shape: top-level `"speed": "fast"` request field + `anthropic-beta:
fast-mode-2026-02-01`. Per official docs
(platform.claude.com/docs/en/build-with-claude/fast-mode): **"up to 2.5x
higher output tokens per second (OTPS)"** on Claude Opus 4.8/4.7, at premium
pricing (2x for Opus 4.8, 6x for Opus 4.7/4.6) — benefits are on OTPS/decode
specifically, "not time to first token." This is exactly the metric this
mission cares about.
  - Model gate confirmed empirically: `model: "claude-sonnet-5"` +
    `"speed":"fast"` → `HTTP 400: "'claude-sonnet-5' does not support the
    speed parameter. This feature is only available on supported models."`
  - Billing gate confirmed empirically: `model: "claude-opus-4-8"` +
    `"speed":"fast"` + the beta header → `HTTP 429: "Usage credits are
    required for fast mode."` — i.e., Fast Mode requires pay-as-you-go/overage
    "usage credits" billing to be enabled on the account, which is separate
    from (and layered on top of) a base Claude subscription. Andrew's account
    does not currently have this enabled (untested whether it's a simple
    console toggle or requires a support/sales conversation — not
    investigated further since enabling it is a billing decision, not ours to make).
  - `cc-xray` confirms Fast Mode is reachable via OAuth/subscription auth
    specifically (its own disabled-reason messaging distinguishes OAuth
    "requires a paid subscription" from API-key "purchase credits" — meaning
    the OAuth path is real and intended, just gated on this specific
    credits flag for us right now), and further gates on: 1P API only (no
    Bedrock/Vertex/Foundry), not combinable with Batch API or a Priority Tier
    commitment, and a server-side rate-limit "cooldown."
  - Citations: platform.claude.com/docs/en/build-with-claude/fast-mode;
    `cc-xray/src/utils/fastMode.ts:41-176`; `cc-xray/src/constants/betas.ts:19`;
    direct probes above (request_id `req_011Cd6jv244xSuxVD58h6dd1` for the
    model-gate 400, `req_011Cd6jw23McxbZEVWpCCkzy` for the credits-gate 429).

### F4. Prompt caching affects TTFT/cost only, never decode throughput (2026-07-17)
Official docs, verbatim: "Prompt caching has no effect on output token
generation. The response you receive is identical to what you would get if
prompt caching were not used." The benefit is confined to input/prefix
processing (better TTFT for long documents). Do not attribute any observed
tok/s difference across trials to cache hit/miss state — if such a
correlation is ever observed, the real cause is elsewhere (most likely
thinking-token proportion, per the Executive Summary's finding #2) and needs
independent investigation. Citation: platform.claude.com prompt caching guide.

### F5. Service tiers / Priority Tier: NOT available to OAuth callers — closed question (2026-07-17)
Priority Tier is an organization capacity commitment (1/3/6/12-month token/min
commitment, purchased via console/sales) — **capacity commitment purchases are
currently closed to new customers entirely** per an active warning banner on
Anthropic's own service-tiers doc, and `claude-code-auth`'s OAuth credentials
have no organization billing entity that could hold one regardless. Matches
100% of observed `usage.service_tier: "standard"` values with zero variance
across every trial this investigation. The only request-settable values are
`service_tier: "auto"` (default) and `"standard_only"` — `"priority"` is a
response-only value never validly sent. **No further investigation needed on
`service_tier` itself** — this question is closed, not open. (Distinct from
Fast Mode, F3, which is a separate mechanism entirely, not implemented via
`service_tier`.) Citation: platform.claude.com/docs/en/api/service-tiers.

### F6. `context-management-2025-06-27` is a context-size/cache-cost tradeoff, not a throughput lever (2026-07-17)
Server-side pre-inference pruning (`context_management: {edits: [...]}`, two
strategies: `clear_tool_uses_20250919` and `clear_thinking_20251015`). No
documented latency/throughput effect in either direction. The real, load-bearing
tradeoff: clearing tool_results or thinking blocks **invalidates the cached
prefix from that point forward**, forcing a fresh (expensive) cache write —
so this beta trades context-window/cost pressure against cache-hit rate, and
is unrelated to decode speed. Citation: platform.claude.com context-editing guide.

### F7. Structured outputs / `output_config.format`: no documented decode-speed effect; grammar-compile latency is real but one-time (2026-07-17)
GA feature (no beta header required currently on most models);
`output_config.format = {"type":"json_schema","schema":{...}}` is genuine
constrained/grammar decoding (not post-hoc validation-retry). Documented
costs: (a) first use of a NEW schema pays extra latency while the grammar
compiles — compiled grammars are cached 24h and reused across calls; (b)
slightly higher input token count from an injected format-explanation system
prompt. **No documented number for steady-state (warm-cache) decode tok/s
impact in either direction** — this remains a genuine open question (see
Open Questions), not a settled "no effect." Claude Code's own 11-beta header
list does not include any structured-outputs beta, so the CLI's own traffic
gives us no data point here. Citation: platform.claude.com/docs/en/build-with-claude/structured-outputs.

### F8. `usage.output_tokens_details.thinking_tokens` — exact thinking/visible split (carried forward, confirmed pre-pass)
Real, present-in-practice response field giving the exact split between
thinking and visible output tokens — used throughout this investigation's
harness (`bench/synthetic_multiturn_test.py`).

### F9. `thinking.display` is a real, request-settable field — confirmed (2026-07-17, TASK-009)
Nested inside the thinking config: `thinking: {"type":"adaptive", "display":
"summarized"}` (or `"omitted"`). Two values: `"summarized"` (readable
thinking text) and `"omitted"` (empty `thinking` field, real `signature`
still present for multi-turn continuity). `"omitted"` is the confirmed
DEFAULT on Sonnet 5, Opus 4.7/4.8, Fable 5, Mythos 5 — a silent change from
Opus 4.6/Sonnet 4.6 where `"summarized"` was default — which is why 100% of
this mission's trials on Sonnet 5 have shown empty visible thinking despite
nonzero billed thinking tokens. Per official docs, `"omitted"` gives a real,
documented streaming-only benefit: **faster time-to-first-VISIBLE-text-token**
("the server skips streaming thinking tokens entirely and delivers only the
signature, so the final text response begins streaming sooner") — but
**this affects only when visible text starts streaming, not total wall-clock
or billing**: "You're still charged for the full thinking tokens. Omitting
reduces latency, not cost." A clean, actionable, previously-undocumented
lever for streaming TTFT specifically — queued as a new experiment below.
Also confirmed same pass: Sonnet 5/Opus 4.7/4.8/Fable 5/Mythos 5 reject
non-default `temperature`/`top_p`/`top_k` with HTTP 400 unconditionally
(closes TASK-006's previously-unconfirmed sampling-restriction claim), and
switching between `adaptive` and `enabled`/`disabled` thinking modes breaks
the messages-side prompt-cache breakpoint (system/tools stay cached) — don't
alternate thinking modes mid-sweep in any experiment or the cache-miss cost
will confound the timing numbers.

### F10. `output_config.effort` measurably changes thinking-fraction, wall-clock, and total decode throughput (2026-07-17, TASK-010)
Resolves the mission's second-highest-priority open question. Sweep:
`bench/specs/effort_sweep_full.json` (low/medium/high/xhigh/max,
`thinking:"adaptive"`, fixed prompt, `max_tokens:6000`, N=3 trials/condition,
raw results `bench/results/effort_sweep_full.jsonl`), plus a follow-up retest
of `max` alone at `max_tokens:20000` (`bench/specs/effort_max_retest.json`,
`bench/results/effort_max_retest.jsonl`) after discovering the first `max` run
was contaminated by truncation (below). Analyzed with `bench/analyze_results.py`
(now reports observed min/max range for n<4 instead of a misleading
zero-width "IQR" — see Methodology Notes).

| effort | median wall_s | median total tok/s | median thinking_frac |
|---|---|---|---|
| low | 15.09s (range 14.23-18.16) | 70.79 (51.25-74.36) | 0.02 (0.00-0.03) |
| medium | 16.62s (range 16.31-24.72) | 68.17 (56.03-68.75) | 0.02 (0.00-0.03) |
| high | 18.78s (range 17.23-20.10) | 69.65 (55.22-74.74) | 0.09 (0.07-0.09) |
| xhigh | 21.33s (range 14.68-39.85) | 76.22 (58.85-101.48) | 0.10 (0.05-0.71) |
| max (clean retest, 20K ceiling) | 64.75s (range 57.07-73.32) | 101.40 (99.78-103.16) | 0.82 (0.81-0.84) |

- **effort is real and substantial, not a no-op**: thinking-token fraction
  rises from ~2% (low/medium) to ~82% (max) of total output, and wall-clock
  rises correspondingly (15s → 65s median) for the identical prompt.
- **A genuine surprise**: raw decode throughput (total tok/s) *increases*
  with effort rather than degrading — low/medium/high/xhigh cluster ~68-76
  tok/s, `max` reaches ~100 tok/s. **Original hypothesis (fixed-overhead
  amortization on longer generations) — CONFIRMED as the dominant mechanism,
  and a competing hypothesis proposed by external peer review (thinking
  tokens simply decode faster than visible text) was tested directly and
  REFUTED. See F12** for the measured breakdown: thinking-token decode is
  actually *slower* than visible-text decode (~90-96 vs ~172-176 tok/s), so
  the rising blended rate is explained entirely by fixed per-request
  overhead (~8-12s, largely connection/TLS/prefill, roughly constant
  regardless of effort) becoming a smaller fraction of a longer call's wall
  time — despite this working *against* the effect (more thinking should
  pull the blend down, not up), overhead dilution still dominates and wins.
- **`max_tokens` headroom is a real operational consideration at high
  effort**: the FIRST `effort:"max"` run (`max_tokens:6000`, same ceiling as
  every other condition) hit `stop_reason:"max_tokens"` in 2 of 3 trials —
  the model was still mid-thought, not naturally done (raw data still in
  `effort_sweep_full.jsonl`, but NOT what's reported in the table above; the
  clean retest at `max_tokens:20000`, all 3 trials `stop_reason:"end_turn"`,
  is). **Any tooling/helper that lets a caller select `effort:"max"` (or
  possibly `"xhigh"` — one of its 3 trials already reached 71% thinking_frac
  at only a 6000-token ceiling) should default to a generous `max_tokens` or
  risk truncating mid-thought** — a real footgun worth guarding against in
  helper tooling, though not a *silent* one: `stop_reason:"max_tokens"` does
  explicitly report it (a caller that checks `stop_reason` will see it; one
  that doesn't may not notice). Correction from an earlier draft of this
  finding, per external peer review: the data prove only that 6000 was
  insufficient for `max`, not that 20000 is the true minimum requirement —
  `xhigh` never truncated even at 6000, so the actual threshold between
  them is unmeasured.
- **`xhigh` showed much higher within-condition variance than low/medium/high**
  (thinking_frac range 0.05-0.71 vs a tight 0.00-0.09 band for the lower
  three) — worth a larger-N follow-up if budget allows; not investigated
  further this pass.
- **Caveat, per external peer review**: n=3/condition is a small sample and
  one of the four non-`max` conditions' three trials was a cache-write (see
  Methodology Notes) — this widens the reported range but, since aggregation
  uses the *median* not the mean, does not shift the reported point
  estimates for these particular runs (verified: the cache-write trial in
  each affected condition was never the median of its 3).

This directly informs Recommended Configuration below. TASK-011's original
design held effort fixed at `high` for the streaming-vs-nonstream test
(the documented Claude Code default, and per this table indistinguishable
in tok/s from low/medium) — but see F11 below for why that result needed
correcting.

### F11. Streaming vs non-streaming, CONTROLLED for effort/thinking — INCONCLUSIVE, corrected after external peer review (2026-07-17, TASK-011)
**This finding was originally reported as "streaming is modestly faster,"
resolving the mission's original trigger question. External peer review
(two independent frontier models, `claude-opus-4-8-thinking-high` and
`gpt-5.6-sol-high` via `cursor-agent`) found a real, verified confound that
the original write-up missed, and the corrected conclusion is: this
comparison did not establish a throughput difference either way.** Kept as
"F11" (not silently rewritten) per this doc's own self-healing convention —
the mistake and its correction are both instructive.

Design: `effort:"high"`, `thinking:"adaptive"`, prompt fixed, N=6
trials/mode, sequential execution
(`bench/specs/task011_stream_vs_nonstream.json` ->
`bench/results/task011_stream_vs_nonstream.jsonl`).

| mode | median wall_s | median total tok/s | mean output_tokens |
|---|---|---|---|
| nonstream | 18.07s (IQR 15.90-19.27) | 67.43 (IQR 66.20-73.18) | 1249.2 |
| stream | 15.61s (IQR 14.99-16.52) | 70.95 (IQR 69.39-72.91) | 1106.5 |

- **The confound, verified directly against the raw data**: nonstream
  trials happened to produce ~11.4% MORE output tokens on average than
  stream trials (1249.2 vs 1106.5) — `adaptive` thinking's own per-call
  variance, not a mode effect. The original write-up's wall-clock comparison
  ("~14% shorter") is mostly explained by this length difference, not by
  streaming being faster — a shorter response takes less wall time
  regardless of mode. The original write-up claimed the output-token
  *ranges* overlapping ruled this out; both peers correctly pointed out this
  is wrong — overlapping ranges say nothing about differing distribution
  centers, which is exactly what's driving the gap here.
- **The rate metric (`total_tok_s`) already divides out length**, and it
  shows only a small, not-statistically-supported difference: medians 67.43
  vs 70.95 (stream ~5% higher), IQRs overlapping substantially. An exact
  permutation test on the median difference (computed independently, not
  taken from either peer) gives **p≈0.33** (924 possible 6-vs-6 splits of
  the 12 trials) — nowhere near significant at this sample size. Both
  external peers independently reached the same qualitative conclusion
  (one computed p≈0.40 via a different method) — convergent evidence from
  two different vendors plus an independent from-scratch check.
- **Honest conclusion**: **no throughput difference between streaming and
  non-streaming was established** by this experiment, in either direction.
  This also means the *original* pre-mission "non-streaming is faster"
  belief is not actually refuted by this data either — both directions are
  genuinely open. A proper re-test needs a fixed-exact-output-length design
  (or a much larger N with interleaved/randomized trial order) — filed as
  **TASK-021**.
- **What remains solidly true**: streaming gives strictly more observability
  — a real, measurable time-to-first-visible-text-token (`ttft_text_ms`,
  ~1.6-3.0s across these trials) that non-streaming cannot report at all.
  That is a genuine, unconfounded advantage regardless of the throughput
  question.

### F12. Thinking-token decode is measurably SLOWER than visible-text decode — refutes a competing hypothesis for F10 (2026-07-17, external-peer-review follow-up)
External peer review proposed an alternative explanation for F10 (rising
blended tok/s with effort): maybe thinking tokens simply decode *faster*
than visible/constrained text, so a higher thinking-fraction mechanically
raises the blend — a different, untested mechanism from this doc's
"fixed-overhead amortization" story. Tested directly: 2 streaming trials at
`effort:"max"`, `thinking:"adaptive"`, `max_tokens:20000`
(`bench/specs/thinking_vs_visible_phase_rate.json` ->
`bench/results/thinking_vs_visible_phase_rate.jsonl`), using the streaming
harness's per-phase timing (`ttft_text_ms` marks the boundary between
"still thinking" and "now emitting visible text").

| trial | thinking tokens | thinking-phase tok/s (approx) | visible tokens | visible-phase tok/s (approx) |
|---|---|---|---|---|
| 1 | 7218 | 90.4 | 1569 | 172.2 |
| 2 | 7705 | 95.8 | 1489 | 175.7 |

- **The peers' hypothesis is REFUTED, in the opposite direction**: visible
  text decodes roughly **1.8-1.9x FASTER** than thinking tokens (~172-176
  vs ~90-96 tok/s), not slower. Thinking tokens are the *slow* phase, not
  the fast one.
- **Caveat on the thinking-phase number**: `thinking.display` defaults to
  `"omitted"` on Sonnet 5 (F9), so thinking content is never streamed as
  visible `thinking_delta` events — `ttft_thinking_ms` (time-to-first-
  thinking-token) is therefore unobservable in this harness, and the
  "thinking-phase" duration used above (`ttft_text_ms` from request start)
  necessarily also includes the initial connection/prefill overhead, not
  purely thinking-generation time. This makes the reported thinking-phase
  rate a conservative *underestimate* of the true thinking-only decode
  rate. Back-of-envelope correction using the ~8-12s fixed-overhead
  estimate implied by F10's low-effort trials still leaves thinking decode
  meaningfully slower than visible decode (~100-106 vs ~172-176 tok/s) —
  the qualitative conclusion is robust to this caveat, even though the
  exact numbers aren't perfectly clean.
- **This actually strengthens F10's amortization story rather than
  competing with it**: since thinking decodes *slower*, a higher
  thinking-fraction should, all else equal, pull the blended rate DOWN, not
  up. That F10's blended rate still rises substantially with effort despite
  this headwind means fixed-overhead dilution isn't just *a* plausible
  explanation — it has to be strong enough to overcome an opposing effect,
  which is a more decisive confirmation than the original write-up claimed.
- Also incidentally confirms the streaming `usage.service_tier` bug (below,
  Methodology Notes) is fixed: both trials correctly show `"standard"`.

### F13. Concurrency: NO detectable per-request throughput degradation up to N=16 simultaneous calls from one account (2026-07-17, TASK-014)
The user's other explicit ask, and both external peers' independent #1 pick
for most valuable remaining experiment. New tool
(`bench/concurrency_test.py`, thread-pool based — `requests` releases the
GIL during I/O, so N threads genuinely overlap N in-flight HTTP calls) fires
N simultaneous non-streaming calls, same prompt/`effort:"high"`/
`thinking:"adaptive"`, at N=1/2/4/8/16 (`bench/results/concurrency.jsonl`).
Run in complete isolation (nothing else hit the API during this test).

| N | individual-call tok/s range | aggregate tok/s | vs N=1 baseline (64.2) |
|---|---|---|---|
| 1 | 64.4 | 64.2 | 1.0x |
| 2 | 67.8-70.8 | 126.0 | 1.96x |
| 4 | 66.9-104.1* | 199.9 | 3.11x |
| 8 | 63.4-74.0 | 503.0 | 7.83x |
| 16 | 56.0-72.9 | 857.7 | 13.4x |

\* one of the 4 calls at N=4 produced 3661 output tokens (vs ~1000-1200 for
its 3 siblings) — ordinary `adaptive`-thinking per-call variance (same
phenomenon seen throughout F10/F11), not concurrency-induced slowdown; it
stretched that level's `batch_span` and thus its aggregate number, which is
why N=4's aggregate looks below the N=8/N=16 trend.

- **Individual per-call tok/s stays in essentially the same band at every
  concurrency level tested** (56-74 tok/s, matching the N=1 baseline of
  64.4 almost exactly) **— no evidence of server-side per-request
  throttling or degradation up to 16 simultaneous calls from one OAuth
  account.** Zero errors, zero 429s, at any level.
- **Aggregate throughput scales substantially with concurrency** — nearly
  linear at N=2/8 (1.96x, 7.83x), a bit sub-linear by N=16 (13.4x, not
  16x). The N=16 shortfall traces to a mild natural stagger in individual
  call completion times (15.5s→21.8s spread across the 16 calls) rather
  than any per-request slowdown — every individual call's own tok/s stayed
  flat regardless of when it finished. This stagger is most plausibly a
  client-side effect (thread scheduling / connection-pool limits in this
  harness) rather than server-side throttling, but that's not yet isolated
  (see caveat below).
- **This is the single most actionable throughput lever this mission has
  found**: unlike Fast Mode (F3, needs a billing change, unverified for us)
  or any single-request parameter tuning (F1/F10), concurrency requires
  ZERO account changes and is available immediately — running N requests
  in parallel gets roughly N times the real-world tokens/sec out of this
  account, at least up to 16x, with no detected ceiling yet.
- **Caveats, stated plainly**: (1) only ONE prompt/effort/thinking
  combination was tested (`effort:"high"`, ~1000-1200 output tokens/call) —
  behavior at much larger per-call output sizes, or under sustained
  concurrent load over minutes rather than one burst, is untested. (2) No
  response headers (rate-limit remaining/reset, retry-after) were captured
  by this harness, so there's no direct evidence of *how much* headroom
  remains above N=16 — only that nothing broke. (3) N was not pushed higher
  than 16 this pass, for time/cost reasons; the ceiling (if any, for this
  account/tier) is still unknown. (4) This test used same-process
  thread-based concurrency only, not separate OS processes — the ticket's
  original design asked for both; given HTTP requests look identical to
  the server regardless of client-side process/thread structure, and the
  actual server-side answer (does concurrent load degrade per-request
  throughput) is what was tested and answered, a process-level variant is
  now a lower-priority follow-up, not a re-test of the same question.

## Rejected / Superseded Hypotheses

- **VPN exit-country geo-mirroring** — hypothesized that Anthropic might route
  inference to region-local compute. Refuted: Anthropic's global endpoint
  "dynamically routes requests to regions with available capacity," decided
  server-side, not client-IP-keyed; ~30 trials across NL/US/KR/direct showed
  no separable effect. (Prior session, `plans/concurrent-napping-wren.md`.)
- **Claude Code CLI's `ttft_ms` anomaly reflects a real network/server issue**
  — refuted: 6 raw wire-level calls (bypassing the CLI) never showed the
  anomaly; it's a CLI-side instrumentation bug. (Prior session.)
- **"Manual `thinking.enabled` mode is unusable on Sonnet 5" (a plausible
  reading of Anthropic's own docs)** — superseded by F2 above: two direct
  OAuth-authenticated probes both returned HTTP 200 for manual mode on
  `claude-sonnet-5`. Kept here explicitly so nobody re-reads the docs, gets
  scared off manual mode, and re-derives the same (refuted-for-us) caution.
- **"Non-streaming is faster than streaming"** — an earlier, pre-mission
  4-trial result suggested this, but it held effort/thinking uncontrolled,
  so it's not trustworthy as stated. This mission's own attempted
  controlled re-test (F11) was ALSO found to be confounded (by output
  length, not by thinking/effort this time) after external peer review —
  so this hypothesis is not so much "refuted" as **genuinely still open**;
  neither direction has a clean, controlled result behind it yet. See F11
  and TASK-021 (the proper re-test). Kept here (rather than deleted) so
  nobody re-derives the old belief from the confounded 4-trial data, or
  mistakes F11's now-corrected write-up for a clean refutation.

## Open Questions

- **Why does Anthropic's documented Sonnet-5 manual-thinking-mode 400
  restriction not manifest on our OAuth traffic (F2)?** STILL UNRESOLVED as
  of TASK-009's follow-up pass — the docs are, if anything, MORE explicit
  than first read: the adaptive-thinking page names Sonnet 5 directly with
  no hedging ("Availability: All models except ... Claude Sonnet 5 ...
  rejected with a 400 error"), yet both this mission's probes and the
  earlier 4-trial pre-compaction test return 200 with real nonzero
  thinking_tokens. TASK-009 surfaced a sharper, not-yet-run disproof test:
  **both trials that got a 200 used a TRIVIAL prompt** (`thinking_tokens:
  0` in this pass's own two probes) — a 200-with-zero-thinking is
  consistent with manual mode being genuinely honored AND with the server
  silently coercing an invalid `type` into adaptive-like behavior. The
  earlier 4-trial test DID use substantive prompts with real nonzero
  thinking_tokens, but nobody has checked whether the *magnitude* scaled
  with the requested `budget_tokens` (proving true compliance) or stayed
  roughly constant regardless of it (implying silent coercion). Proposed
  test, not yet run: same complex prompt at `budget_tokens=1024` vs
  `budget_tokens=32000` — if thinking_tokens scales with the budget, manual
  mode is genuinely honored for us; if it doesn't, Sonnet 5 is silently
  ignoring `enabled` and running adaptive-equivalent behavior under a
  lenient, non-erroring OAuth path. cc-xray cross-check (TASK-009):
  confirmed zero OAuth-vs-API-key branching anywhere near thinking-mode
  logic, and zero changelog evidence of auth-method-specific enforcement —
  so "OAuth is simply more lenient" remains unconfirmed, not refuted.
- ~~Does `output_config.effort` measurably change tok/s, thinking-token
  fraction, and wall-clock, holding everything else fixed?~~ — **RESOLVED,
  see F10**: yes, substantially — thinking_frac 2%→82% low→max, wall-clock
  15s→65s median, and total tok/s counterintuitively *rises* with effort
  (~69-76 → ~100 tok/s). Mechanism confirmed directly (F12): thinking tokens
  decode *slower* than visible text (~90-96 vs ~172-176 tok/s), so the
  rising blend is fixed-overhead dilution overcoming an opposing effect,
  not a "thinking decodes faster" story. `max_tokens` headroom is a real
  operational consideration at `effort:"max"` (6000 was shown insufficient;
  20000 sufficed in the retest, but the true minimum is unmeasured).
- **Streaming vs non-streaming, CONTROLLED for effort/thinking** — the
  original question that triggered this whole mission. **STILL OPEN.**
  This mission's first attempt (F11) claimed "streaming is modestly
  faster," but external peer review found the comparison was confounded by
  an ~11% output-length difference between conditions (verified directly
  against the raw data, plus an independent permutation test, p≈0.33 — not
  significant at n=6/mode). Neither direction is currently well-supported.
  **Filed as TASK-021**: re-test with either a fixed-exact-output-length
  prompt design, a much larger N, or randomized/interleaved trial order (or
  some combination) to remove the confound properly.
- **Does `redact-thinking-2026-02-12` (sent unconditionally by Claude Code)
  actually cause the redacted/empty-visible-thinking behavior we've seen in
  100% of trials so far, or is that a model default independent of the
  beta?** Proposed test: identical request with vs without this beta header,
  holding thinking mode fixed, compare `ttft_thinking_ms`/visible thinking
  chars.
- ~~Does `thinking.display` exist as a request-settable field~~ — **RESOLVED,
  see F9**: yes, confirmed request-settable, `"omitted"`/`"summarized"`,
  streaming-TTFT-only effect (no change to total tokens or cost). **Still
  open**: the actual A/B has not been run — measure streaming
  time-to-first-visible-text-token with `display: "omitted"` vs
  `"summarized"`, holding effort/thinking-type/prompt fixed, to confirm the
  documented benefit materializes for us and to size it.
- **Does `token-efficient-tools-2026-03-28` measurably reduce input tokens
  and/or wall-clock on a tool-call-heavy synthetic history?**
- **Does 1h vs 5m cache TTL reduce prefill/TTFT *variance* across a
  long-running session** (not disputed: caching doesn't touch decode, per
  F4 — this question is narrowly about TTFT variance over a session with
  idle gaps, e.g. does a 1h TTL avoid re-paying cache-write cost across a
  6-8 minute gap that would blow past a 5m TTL)? Proposed test: repeated
  trials with deliberate idle gaps straddling the 5m boundary, both TTLs,
  compare `cache_creation_input_tokens` incidence and TTFT.
  Also open: whether `cache_control.scope: "global"` (an internal,
  undocumented-on-the-public-caching-page field Claude Code sends via
  `prompt-caching-scope-2026-01-05`) is even accepted for a single-user OAuth
  caller, and if so whether it does anything observable for us (a priori:
  probably not, since the mechanism exists to share a cache ACROSS multiple
  users of one org, which doesn't apply to a lone subscriber).
- ~~Does concurrency (N parallel raw API calls) change per-request tok/s~~ —
  **RESOLVED, see F13**: no, not detectably, up to N=16 same-process
  concurrent calls (individual tok/s stayed in the same 56-74 band as the
  N=1 baseline at every level tested). Aggregate throughput scales
  substantially (~13.4x at N=16). Still open: the actual ceiling (untested
  above N=16), behavior under sustained load over minutes, and a
  separate-OS-process variant (lower priority — see F13's caveats).
- **Does grammar-constrained decoding (structured outputs, warm cache) change
  steady-state decode tok/s at all** — no data in either direction (F7).
- **Does `inference_geo` (an explicit, documented per-request region-override
  field, distinct from the already-closed client-IP VPN question) change
  TTFT or decode speed?**
- **Does `cache-diagnosis-2026-04-07` do anything observable** — zero
  documentation found anywhere; propose sending it alone on an otherwise
  normal request and diffing the response/usage shape against a baseline.
- **Does connection/session reuse (a persistent HTTP session or HTTP/2
  connection pool, vs this harness's current fresh `requests.post` per
  trial) measurably reduce per-call fixed overhead?** Raised independently
  by BOTH external peer reviewers (convergent, different vendors) — this
  investigation has never varied transport-level connection reuse at all,
  and F10/F12 together imply a real ~8-12s fixed per-request overhead
  (connection setup + TLS + prefill) that a persistent connection could
  plausibly shrink. This is a previously entirely-unconsidered lever, not a
  refinement of an existing one. **Filed as TASK-020.**

## Recommended Configuration

*(firming up as experiments land)*

- **Run work concurrently — this is the single biggest lever found so far
  (F13)**: up to 16 simultaneous calls from one account showed no
  detectable per-request throughput degradation, and aggregate throughput
  scaled ~13.4x. If a workload can be parallelized at all (independent
  prompts, independent sub-tasks), doing so multiplies real-world
  tokens/sec with zero account/billing changes — a bigger, more certain win
  than Fast Mode's unverified 2.5x. Ceiling above N=16 is untested; if
  higher concurrency is needed in practice, verify it holds at that scale
  first rather than assuming linear scaling continues indefinitely.
- **Streaming vs non-streaming: no throughput preference established yet**
  (F11, corrected after external peer review) — the mission's original
  attempt at this comparison was confounded by an output-length difference
  between conditions; re-test pending (TASK-021). Streaming's one
  clearly-supported advantage is observability (a real, measurable
  TTFT-to-visible-text via `ttft_text_ms`, unavailable in non-streaming) —
  prefer it for that reason, not for an unestablished speed advantage.
- **Prefer `thinking: {"type":"adaptive"}` + `output_config.effort` over
  manual `budget_tokens`** for new experiments and for any convenience helper
  shipped in this library, since it's very likely what Claude Code itself
  actually sends on Sonnet-5-class models, and it's the officially-supported
  path going forward — even though manual mode still empirically works today (F2).
- **`effort: "high"` (the documented default) is a sound default for typical
  interactive use** (F10): low/medium/high/xhigh are statistically
  indistinguishable in tok/s (~68-76 tok/s) and low/medium/high cluster in a
  tight, low thinking-fraction band (~2-9%). Reach for `max` only when the
  task genuinely needs deep, extended reasoning — it triples wall-clock
  (~65s vs ~15-20s for the same prompt) and spends ~82% of output on
  invisible thinking tokens.
- **Any helper exposing `effort` must default/validate `max_tokens` >= ~20000
  when `effort` is `"xhigh"` or `"max"`** (F10) — otherwise a real request can
  silently truncate mid-thought (`stop_reason:"max_tokens"`) with no error,
  which happened in 2 of 3 trials at a 6000-token ceiling during this mission's
  own sweep.
- **Fast Mode (F3) is the single biggest concrete lever this mission has
  found, and it needs Andrew's decision, not this session's.** Enabling
  pay-as-you-go "usage credits" billing unlocks up to a documented 2.5x OTPS
  on Opus 4.7/4.8 at 2x-6x premium pricing. This session will not enable
  billing changes unilaterally — flagging clearly for Andrew to evaluate the
  cost/benefit himself. If he opts in, re-run this mission's Fast Mode probe
  to confirm activation, then benchmark it properly against a same-model
  non-fast baseline.
- **Don't chase `service_tier`/Priority Tier** (F5) — confirmed closed to us,
  don't re-investigate.
- **Prompt caching should be treated purely as a cost/TTFT optimization in
  any helper tooling** — never marketed or reasoned about as a decode-speed
  lever (F4).

## Methodology Notes

- Raw-API testing uses `~/Projects/cc/claude-code-auth` (OAuth credential
  sharing with the real Claude Code subscription — no separate API key/billing).
- **The OAuth attribution/fingerprint system block is mandatory** for any raw
  call to succeed — see `src/claude_code_auth/fingerprint.py`. Omitting it
  produces a bare 429 with no rate-limit headers, which looks like a quota
  problem but isn't.
- Every experiment that isn't a one-line config toggle should be reproducible
  from a committed script under `bench/` — not a one-off `/tmp` script that
  evaporates. `bench/synthetic_multiturn_test.py` now supports `--thinking
  {enabled,adaptive,disabled}`, `--effort`, `--speed`, `--service-tier`,
  `--inference-geo`, `--extra-beta` (repeatable), `--model`, and `--cache-ttl`
  as of 2026-07-17, so most single-lever A/Bs are just different flag
  combinations against the same harness — see `bench/README.md`.
- Real-money real-API calls: keep a running approximate cost tally in this
  doc's changelog so the scope of spend stays visible.
- **Serialize real-API measurement experiments to avoid a concurrency
  confound.** Several open questions above are single-lever A/Bs (effort,
  caching, redact-thinking, etc.) that assume no OTHER concurrent load is
  hitting the same account at the same time — and the concurrency question
  itself (does parallel load change per-request tok/s?) is one of the things
  under investigation. Running multiple experiment forks in parallel, each
  hitting the live API, would contaminate every other experiment's numbers
  with an uncontrolled concurrency effect. Single-lever experiments run
  SERIALLY (one at a time, coordinator-run or one dedicated fork); the
  concurrency experiment gets its OWN isolated wave with nothing else calling
  the API at the same time.
- **`bench/analyze_results.py`'s `median_iqr` collapsed to a fake zero-width
  "IQR" for n<4 trials** (fixed 2026-07-17, TASK-010) — since n=3 is this
  project's default trial count (cost control), every sweep run before the
  fix would have silently under-reported real trial-to-trial variance as
  "no spread." Now n<4 reports observed min/max labeled "range" instead of a
  misleading "IQR". Re-check any pre-fix sweep output before trusting its
  reported spread.
- **Cache-write cost is incurred by whichever trial happens to run first
  after a thinking-mode/cache-lineage change, not evenly spread** — in the
  effort sweep, exactly 4 of 15 trials paid the full 358K-token cache-write
  cost (~6x a cache-read's price) simply by being first in their condition
  group after a cache miss; the other 11 were cheap cache-reads. Not a bug,
  but worth deliberately exploiting going forward: for any future sweep, run
  one cheap throwaway call first (trivial prompt, minimal `max_tokens`) to
  eat the cache-write cost on a near-zero-output request, so every real
  measurement trial is a cache-read. Not done retroactively here since the
  actual spend was still modest (see Changelog cost tally).
- **A real bug in `bench/synthetic_multiturn_test.py`'s streaming usage
  assembly, found via external peer review and fixed 2026-07-17**: the
  `message_delta` SSE handler did `usage_final = evt.get("usage") or
  usage_final` — a wholesale REPLACE, not a merge. Anthropic's
  `message_delta` usage payload carries updated `output_tokens` etc. but
  does not repeat `message_start`-only fields like `service_tier`, so every
  streaming trial in this mission silently lost `service_tier` from its
  recorded usage (verified: all 6 `stream-high` trials in
  `task011_stream_vs_nonstream.jsonl` show `service_tier: ""`, while their
  non-streaming siblings correctly show `"standard"`). Fixed to merge
  (`usage_final = {**(usage_final or {}), **delta_usage}`); verified fixed
  via a fresh streaming call showing `service_tier: "standard"` (see F12's
  data). **F5's "100% standard, zero variance" claim was only ever actually
  verified for non-streaming trials** — worth re-confirming for streaming
  specifically if it matters later, though no reason to expect it differs.
- **`bench/run_sweep.py`'s `DEFAULT_ARGS` and `bench/synthetic_multiturn_test.py`'s
  own `--thinking` argparse default both defaulted to the legacy manual
  `"enabled"` mode**, silently contradicting this doc's own Recommended
  Configuration (prefer `adaptive`+`effort`). Every actual experiment this
  mission ran explicitly overrode `thinking` in its spec, so no existing
  result was affected — but the mismatch was a latent footgun for any
  future spec that omits `thinking`. Fixed 2026-07-17 (external peer
  review): both now default to `"adaptive"`.
- **External peer review, 2026-07-17**: consulted `claude-opus-4-8-thinking-high`
  and `gpt-5.6-sol-high` via `cursor-agent -p -f --mode ask` at this
  mission's natural checkpoint (after F10/F11 landed), per Andrew's original
  request for anti-cargo-cult review. `claude-fable-5-thinking-high` failed
  with a data-retention-policy acknowledgment gate this session couldn't
  clear non-interactively (`ActionRequiredError: Review Data Policy`) — not
  pursued further given time constraints; 2 of 3 peers still gave
  substantive, independently convergent findings. Every peer finding
  reported as "confirmed" in this doc was independently re-verified against
  the raw data or source code before being acted on — not taken on either
  peer's word alone, same standard applied throughout this investigation to
  Anthropic's own docs (F2). See the Changelog entry below for the full
  list of corrections this produced.

## Changelog

- 2026-07-17 (TASK-014, coordinator-run, fully isolated): New tool
  `bench/concurrency_test.py` (thread-pool based). Tested N=1/2/4/8/16
  simultaneous same-account calls. Landed F13: no detectable per-request
  throughput degradation at any level tested (individual tok/s stayed
  56-74, matching the N=1 baseline of 64.2); aggregate throughput scaled
  ~13.4x by N=16. This is the single most actionable lever this mission has
  found — free, immediate, no billing changes. Both external peers had
  independently picked concurrency as the highest-value remaining
  experiment; this confirms why. Real-API spend: 31 calls total
  (1+2+4+8+16), ~1000-1300 output tokens each (~35K total output tokens),
  effort=high/cache-read throughout — roughly $0.50-0.70.
- 2026-07-17 (external peer review pass, natural checkpoint after F10/F11):
  Consulted 2 of 3 planned external peers (opus-4.8, gpt-5.6-sol; fable-5
  blocked by a data-policy gate) with an explicit anti-cargo-cult framing.
  Both independently found the SAME real flaw in F11 (output-length confound
  between stream/nonstream conditions) — verified first-hand against the raw
  data (nonstream mean 1249.2 vs stream mean 1106.5 output tokens) and with
  an independent permutation test (p≈0.33). **Corrected F11 from "streaming
  is modestly faster" (confirmed) to inconclusive** — the mission's original
  question is reopened, re-test filed as TASK-021. Also acted on: **F12**
  (new finding — tested and refuted the peers' alternative "thinking decodes
  faster" hypothesis for F10; thinking is actually ~1.8-1.9x *slower* than
  visible text, which if anything strengthens F10's fixed-overhead story);
  fixed a real streaming-usage-merge bug that silently dropped
  `service_tier` from every streaming trial's usage record; fixed
  `bench/run_sweep.py`/`bench/synthetic_multiturn_test.py`'s default
  `thinking` mode (was `"enabled"`, silently contradicting this doc's own
  recommendation; now `"adaptive"`); added a cache-write-contamination flag
  to `bench/analyze_results.py`'s report (mirrors the existing
  max_tokens-truncation flag); softened two overclaims from the original
  F10 write-up (`"silent"` truncation → truncation is reported via
  `stop_reason`, just easy to miss; `">=20000" max_tokens requirement` →
  only proven sufficient, not proven minimal). Filed **TASK-020**
  (connection/session-reuse — a genuinely new lever, raised independently by
  both peers) and **TASK-021** (properly controlled streaming-vs-nonstream
  re-test) at appropriate priority. Real-API spend: 2 trials for F12
  (~17.7K output tokens, effort=max, cache-read — negligible, well under
  $1); the two peer-review CLI calls themselves are separate Cursor/OpenAI
  billing, not Anthropic API spend, and not tracked in this tally.
- 2026-07-17 (TASK-011, coordinator-run serial experiment): Ran the
  mission's original question properly, controlled this time: streaming vs
  non-streaming at fixed `effort:"high"` + `thinking:"adaptive"`, N=6
  trials/mode, same prompt (`bench/specs/task011_stream_vs_nonstream.json`
  -> `bench/results/task011_stream_vs_nonstream.jsonl`). Landed F11:
  streaming is modestly FASTER (median wall_s 15.61s vs 18.07s nonstream),
  refuting the earlier confounded belief. Real-API spend: 12 trials, all
  cache-reads (effort:"high"'s cache lineage was already warm from TASK-010,
  run minutes earlier, well within the 1h TTL) — no new cache-write cost,
  roughly $0.20-0.30 in output tokens only.
- 2026-07-17 (TASK-010, coordinator-run serial experiment): Ran the
  `output_config.effort` sweep (low/medium/high/xhigh/max, N=3 trials each,
  `bench/specs/effort_sweep_full.json` -> `bench/results/effort_sweep_full.jsonl`).
  Landed F10: effort is real and substantial (thinking_frac 2%→82%,
  wall-clock 15s→65s, tok/s counterintuitively *rises* with effort).
  Discovered `effort:"max"` truncated (2/3 trials, `stop_reason:"max_tokens"`)
  at the sweep's 6000-token ceiling; retested `max` alone at
  `max_tokens:20000` for a clean read (`bench/specs/effort_max_retest.json` ->
  `bench/results/effort_max_retest.jsonl`, all 3 trials natural `end_turn`).
  Also fixed a real bug found while reading the raw data:
  `bench/analyze_results.py`'s `median_iqr` silently collapsed to a
  zero-width fake "IQR" for n<4 (this project's default trial count),
  masking genuine variance — now reports observed min/max range for small n,
  plus a truncation warning when any trial in a group hit `max_tokens`. This
  resolves the mission's second-highest-priority open question and directly
  unblocks TASK-011. Real-API spend this experiment: 18 trials, ~52.7K output
  tokens total, only 4/18 trials paid a full cache-write (the rest were
  cheap cache-reads) — roughly $10-11 (rough estimate; largest single spend
  of this mission so far, previous total was ~$8-10). Methodology
  improvement noted for future sweeps: a cheap cache-warmup call before a
  real batch would move the cache-write cost off real measurement trials.
- 2026-07-17 (wave 2): TASK-007 fixed for real — root cause was a stale
  `token_url` (`console.anthropic.com` → `platform.claude.com`), confirmed
  against `cc-xray`'s `PROD_OAUTH_CONFIG.TOKEN_URL`; the bug had been
  MASKED by an existing test that asserted the wrong URL as correct, not
  just left unfixed — both the assertion and the underlying config are now
  fixed, plus a new direct regression test. `refresh_margin_ms=60_000`
  workaround removed from `bench/synthetic_multiturn_test.py`, verified via
  a real local `access_token` retrieval with default settings. TASK-008
  landed `bench/run_sweep.py` (serial sweep runner, `--dry-run` validated,
  zero network calls in that mode by construction) and
  `bench/analyze_results.py` (median/IQR aggregator, validated against a
  hand-computed fixture — matched exactly). TASK-009 confirmed `thinking.display`
  is real and request-settable (F9) but left the F2 contradiction
  unresolved, sharpening it into a concrete next test (budget-scaling A/B).
  Process note: two of three wave-2 forks hit severe context exhaustion
  (~300-308K tokens each) partly caused by a coordinator mistake —
  `isolation: "worktree"` was requested for cross-repo forks, which mirrors
  the *coordinator's* tracked repo (`~/Projects/arr`) rather than the
  target (`~/Projects/cc/claude-code-auth`), forcing both forks to
  self-diagnose and improvise their own worktree-on-the-right-repo
  workaround before they could even start their real task. One fork
  (TASK-007) also left the shared `~/Projects/cc/claude-code-auth` checkout
  on a stray branch with zero commits — caught and fixed immediately
  (checked out back to `main`, confirmed clean). Both forks' partial,
  well-diagnosed work was finished by the coordinator directly rather than
  re-dispatching (small, already-scoped remainders). Lesson for future
  waves in this mission: never pass `isolation: "worktree"` for a fork
  targeting a different repo than the coordinator's own cwd — either omit
  isolation and brief the fork to make its own `git -C <target> worktree
  add` on the correct repo, or dispatch from a shell already cd'd into the
  target repo.
- 2026-07-17 (wave 1 + coordinator checks): Wave 1 research landed (6 parallel
  forks, TASK-001..006) — catalogued ~20 anthropic-beta values beyond the
  known 11 (including `fast-mode-2026-02-01`, `redact-thinking-2026-02-12`,
  `token-efficient-tools-2026-03-28`); confirmed the `effort` request field
  (F1); found and empirically resolved a docs-vs-reality contradiction on
  manual thinking mode (F2, 2 real API calls, ~700 tokens total, negligible
  cost); confirmed Fast Mode's existence and its two gates via 2 direct
  probes (F3, 1 real Opus-4.8 call at max_tokens=50, negligible cost);
  confirmed prompt caching is TTFT/cost-only (F4); closed the service-tier
  question definitively (F5); classified context-management as a
  cache-cost tradeoff not a throughput lever (F6); documented structured
  outputs' grammar-compile-latency-only claim (F7). Migrated the bench
  harness from `/tmp` into `bench/` (commit `54064b5`) and fixed a real,
  pre-existing broken `uv run pytest` gate (stale venv shebang from a
  different, since-deleted project directory) as part of that migration.
  Upgraded `bench/synthetic_multiturn_test.py` to a full argparse CLI
  supporting thinking mode, effort, speed, service_tier, inference_geo, and
  arbitrary extra beta headers, to serve as the harness for the next wave of
  experiments. Running cost tally: ~$8-10 (prior session + this mission's
  own testing) + negligible (<$0.05) for this pass's 3 probe calls.
- 2026-07-17: Doc created; autonomous 8h research+build pass kicked off
  (autopilot + parallel-orchestration skills). Prior session's two headline
  results (VPN null result, thinking-token-proportion finding) carried
  forward as the starting evidence base.
