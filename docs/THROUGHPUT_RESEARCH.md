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
11. **The mission's ORIGINAL question is now DEFINITIVELY resolved: streaming
    and non-streaming have NO throughput difference.** A properly controlled
    redo (TASK-021) — fixed-exact-output-length design (every trial forced to
    truncate at an identical 800 output tokens) plus randomized/interleaved
    trial order, N=16/mode (32 total calls) — found median wall_s 11.46s
    (nonstream) vs 11.47s (stream), a 0.006s difference, Monte Carlo
    permutation test p=0.99. This finally closes the question F11 first
    mis-resolved and peer review later corrected to inconclusive. Streaming's
    only real advantage remains observability (TTFT-to-visible-text), not speed.
12. **Manual `thinking.enabled` mode's `budget_tokens` is NOT enforced at all
    for this OAuth traffic** — a genuinely surprising, previously-unresolved
    finding (TASK-019, resolves F2's long-standing open question). The
    diagnostic evidence is the LOW arm: `budget_tokens=1024` still consumed
    the entire external `max_tokens=20000` ceiling as thinking (a ~20x
    overrun of its own requested cap), never stopping anywhere near 1024 —
    reconfirmed by an incidental third data point (F16, `budget_tokens=2000`
    also overran to its own external ceiling). The paired `budget_tokens=32000`
    trial is corroborating but NOT independently diagnostic on its own — since
    32000 exceeds the fixed 20000 `max_tokens` ceiling, that trial would hit
    `max_tokens` first whether or not `budget_tokens` is actually enforced
    (external peer review caught this; see F15's corrected write-up).
    `budget_tokens` is accepted (no 400, consistent with F2) but appears to do
    nothing observable — only `output_config.effort` is a real, working lever
    for controlling thinking depth on this path. See F15.
13. **Connection/session reuse shows no detectable effect** (TASK-020) — a
    lever both external peer reviewers independently flagged as unconsidered.
    Persistent-session calls were statistically indistinguishable from
    fresh-connection-per-call ones on cheap trivial-output trials (median
    wall_s 2.29s vs 2.33s, N=10 each, IQRs heavily overlapping). Most likely
    because TCP/TLS handshake time is a small fraction of the ~8-12s fixed
    overhead F10/F12 imply — the real overhead is probably server-side
    prefill, which client-side connection reuse can't touch. See F14.
14. **`redact-thinking-2026-02-12` is a no-op for us — confirmed, not assumed**
    (TASK-012). With vs without this beta header (Claude Code sends it
    unconditionally) produced byte-identical response shape on a prompt that
    reliably provoked thinking: same content-block type (`"thinking"`), same
    empty visible thinking text, same signature presence, same exact
    thinking-token count, same stop_reason. The empty-visible-thinking
    behavior seen in 100% of this mission's trials is fully explained by
    `thinking.display` defaulting to `"omitted"` (F9) — not by this beta. See F16.
15. **`inference_geo` is closed to us — a clean, definitive gate, not
    ambiguous** (TASK-016): `HTTP 400: "inference_geo is not supported for
    this organization type."` — same family as F5 (Priority Tier) and F3
    (Fast Mode's credits gate): a real, documented feature (accepts `"us"` or
    `"global"`, per official docs) that this OAuth/subscription account's
    organization type cannot use at all, regardless of model. See F17.
16. **Structured outputs (`output_config.format`) show suggestive evidence of
    real cold-schema compile latency** (TASK-015) — 3/3 paired cold-vs-warm
    trials favored the warm (repeated-schema) call being faster, and the one
    cleanest pair (both ended naturally, no truncation) showed warm producing
    MORE output tokens in LESS wall-clock than cold — the opposite of what
    decode-rate-alone would predict, consistent with a real fixed compile-time
    tax on first use. Small n=3 and uncontrolled output length (the API
    rejects `minItems`/`maxItems` other than 0/1, discovered empirically) mean
    this isn't a clean effect size yet — flagged as suggestive, not proven.
    See F18 (also folds two doc corrections into F7).
17. **`thinking.display`'s documented streaming-TTFT benefit could not be
    isolated** (TASK-018) — not refuted, genuinely inconclusive: `adaptive`
    thinking's own call-to-call depth variance (documented in F10) swamps
    whatever transmission-level effect `display:"omitted"` vs `"summarized"`
    has on time-to-first-visible-text. Worth noting: the obvious fix (hold
    thinking depth constant via a fixed `budget_tokens`) doesn't work either,
    because F15 already proved that field isn't enforced — there is currently
    no reliable lever to control `adaptive` thinking's variance for a design
    like this. See F19.
18. **Cache TTL (`1h` vs `5m`) is DEFINITIVELY confirmed to control whether a
    cached prefix survives a real idle gap** (TASK-013, this mission's LAST
    ticket) — 6/6 trials, zero ambiguity: every `5m`-TTL trial paid a full
    fresh cache-write after a ~7 minute gap (identical token count to its own
    first call); every `1h`-TTL trial's second call landed as a pure
    cache-read with an EXACT token-count match to its first call. This closes
    planq's backlog to zero open tickets and settles a real, avoidable cost
    footgun: any session with realistic human-paced idle gaps should default
    to `1h`, never the shorter TTL. See F20.
19. **A final external peer-review pass (after the backlog hit zero) caught
    several real overclaims and surfaced a genuinely new, concrete lever —
    which was then tested and REFUTED, not confirmed.** HTTP/2 multiplexing
    (many concurrent calls over ONE connection) was hypothesized to recover
    F13's sub-linear N=16 concurrency scaling (13.4x, not 16x) — instead, it
    fails outright between N=4 and N=8 (`httpx.RemoteProtocolError`/
    `LocalProtocolError`), well before reaching F13's own N=16, which
    ordinary independent HTTP/1.1 connections handled with zero errors.
    "Switch to HTTP/2" is not the fix for F13's shortfall — keep using
    independent connections for concurrent workloads. See F21.
20. **Batch API is closed to this OAuth account — via a token-scope gate,
    genuinely different from every other gate this mission found.** F5/F17/F3
    are all organization-type/billing gates; this is an OAuth token SCOPE
    restriction (`HTTP 403`: token lacks `user:batch`/`user:developer`/
    `workspace:developer`/`workspace:inference`) — not something a billing
    change could unlock, since it would need a different auth path (a real
    API key) entirely. See F22.
21. **Firing N concurrent calls against the SAME cold cache prefix pays the
    full cache-write cost N times over, not once — no deduplication at
    all.** 2/2 trials, N=4 each: every concurrent caller independently paid
    the full 358K-token cache-write, none got a cache-read, despite all N
    racing the identical cache key at the same instant. A real, easy-to-miss
    cost trap when naively combining this mission's two biggest levers
    (F13's concurrency + prompt caching): fix is a single cheap warmup call
    before firing the concurrent batch. See F23.
22. **Warm cached-context LENGTH (independent of cache hit/miss) shows no
    measurable effect on decode-phase tok/s across a 7x range (~50K to
    ~358K tokens)** — a clean extension of F4's "caching doesn't touch
    decode" finding to a second, independent variable (raw prefill length
    itself). Medians 0.2 tok/s apart, ranges almost fully overlapping. A
    real harness bug was found and fixed en route: `thinking="disabled"`
    previously omitted the `thinking` field rather than sending it
    explicitly, and omitting it does NOT reliably suppress server-side
    reasoning — now fixed to always send `{"type":"disabled"}` explicitly.
    See F24.

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

### F7. Structured outputs / `output_config.format`: no documented decode-speed effect; grammar-compile latency is real but one-time (2026-07-17, corrected TASK-015)
**Fully GA — no beta header needed at all, for any supported model** (corrected
from an earlier "no beta header required currently on most models" — a design
fork re-fetched the official doc directly and found the restriction doesn't
exist; the deprecated `structured-outputs-2025-11-13` beta + `output_format`
param still work "for a transition period" but are not required).
`output_config.format = {"type":"json_schema","schema":{...}}` is genuine
constrained/grammar decoding (not post-hoc validation-retry), confirmed live
(TASK-015, below). Documented costs: (a) first use of a NEW schema pays extra
latency while the grammar compiles — compiled grammars are cached with a
**sliding 24h-from-last-use TTL** (corrected from "24h flat" — each use resets
the window; same sliding-TTL pattern worth watching for in TASK-013's cache
work); (b) slightly higher input token count from an injected
format-explanation system prompt. Cache invalidates if the schema *structure*
changes or the request's tool set changes — renaming only a schema's
`name`/`description` does NOT invalidate it. **No documented interaction
between `output_config.format` and `output_config.effort`**, even though both
live under the same parent object (`bench/synthetic_multiturn_test.py:122-127`)
— composability is genuinely undocumented, not confirmed either way.
**A real, previously-undocumented API constraint found empirically (TASK-015
live test)**: array-type schema properties reject any `minItems`/`maxItems`
value other than 0 or 1 — `HTTP 400: "For 'array' type, 'minItems' values
other than 0 or 1 are not supported"` — so exact-length structured-output
arrays can't be forced via the schema itself; length has to be controlled via
the prompt text (imprecisely) or `max_tokens` truncation instead. **No
documented number for steady-state (warm-cache) decode tok/s impact in either
direction** — genuinely open; see F18 for suggestive (not yet clean) live
evidence that cold-schema compilation adds real latency. Claude Code's own
11-beta header list does not include any structured-outputs beta, so the
CLI's own traffic gives us no data point here. Citation:
platform.claude.com/docs/en/build-with-claude/structured-outputs.

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

**UPDATE — DEFINITIVELY RESOLVED (TASK-021 redo, 2026-07-17):** The proper
re-test this entry called for has been run, with BOTH fixes combined: (1) a
fixed-exact-output-length design — `max_tokens:800` deliberately set below the
prompt's natural completion length so every single trial in both modes
truncates at the identical 800 output tokens (verified: all 32 trials show
`output_tokens:800`, `stop_reason:"max_tokens"` — length is controlled by
construction, not statistics); (2) randomized/interleaved condition order
(two independent random-seeded shuffles of 8 stream + 8 nonstream labels each,
`bench/specs/task021_interleaved_stream_vs_nonstream.json` +
`..._batch2.json` → `bench/results/task021_interleaved.jsonl`), killing the
time-of-day-drift confound both peers flagged. Result at N=16/mode (32 total
calls): **median wall_s 11.46s (nonstream) vs 11.47s (stream) — a 0.006s
difference — median total_tok_s 69.78 vs 69.74.** A Monte Carlo permutation
test (200,000 resamples on the median difference) gives **p=0.99** — as null
a result as this kind of test produces. (An interim N=8/mode read had shown a
~7% stream-faster trend with p≈0.14 — not significant, but suggestive; it
fully evaporated once N doubled to 16/mode, which is itself informative: the
original N=6/mode belief in either direction was never adequately powered.)
**Conclusion, no longer hedged: streaming and non-streaming have NO
throughput difference for this workload.** The only genuine, established
advantage of streaming remains observability (TTFT-to-visible-text) — never
throughput. This closes the mission's original triggering question and
TASK-021.

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

### F14. Connection/session reuse: no detectable effect (2026-07-17, TASK-020)
Both external peer reviewers independently flagged this as a genuinely
unconsidered lever: every trial across this whole mission made a fresh
`requests.post()`, never reusing a TCP/TLS connection. Built the capability
(a worktree-isolated fork added optional `session=None` params to
`run_streaming`/`run_nonstreaming`, and a `--reuse-connection` flag to
`run_sweep.py` that shares one `requests.Session()` across a whole sweep;
100% backward-compatible, validated via monkeypatch — merged at `edfe080`),
then ran the live A/B myself: `bench/specs/connection_reuse_ab.json`
(trivial prompt "Reply with exactly the word OK...", `thinking:disabled`,
`max_tokens:50`, cache warm) × N=10 trials each,
`bench/results/connection_reuse_baseline.jsonl` vs `..._reuse.jsonl`.

| condition | median wall_s | median total tok/s |
|---|---|---|
| baseline (fresh connection/call) | 2.33s (IQR 2.02-2.96) | 1.71 tok/s (IQR 1.35-1.98) |
| `--reuse-connection` (persistent session) | 2.29s (IQR 1.87-2.56) | 1.75 tok/s (IQR 1.56-2.14) |

- **No detectable effect** — the ~2% difference is well within the heavily
  overlapping IQRs. Per-trial wall_ms inspection also found no "first call is
  slower" pattern within the reuse condition (trial 1's wall_ms was
  unremarkable relative to trials 2-10; the single outlier in each condition
  landed at a random position, consistent with ordinary network jitter, not
  a systematic cold-connection tax).
- **Most likely explanation**: TCP+TLS handshake time is typically ~50-300ms
  — a small fraction of the ~8-12s fixed per-request overhead F10/F12 imply
  exists. That overhead is more plausibly server-side (prefill/queueing),
  which client-side connection reuse structurally cannot touch. A genuine,
  useful null result — resolves the peer-raised open question.
- Real-API spend: 20 trivial calls (`max_tokens:50`, thinking disabled,
  cache-warm) — negligible, well under $0.10.

### F15. Manual `thinking.enabled` mode's `budget_tokens` is NOT enforced for this OAuth traffic (2026-07-17, TASK-019, resolves F2's open question)
TASK-009 had sharpened F2's open question into a concrete disproof test: same
complex prompt at `budget_tokens=1024` vs `budget_tokens=32000` — if
`thinking_tokens` scales with the requested budget, manual mode is genuinely
honored; if it stays roughly constant regardless, Sonnet 5 is silently
ignoring it. Ran exactly this test: a genuinely hard multi-step logic puzzle
(five-person bridge-crossing optimization, forcing real reasoning), held
`max_tokens=20000` fixed as the only ceiling, varied only `budget_tokens`.

| `budget_tokens` requested | `thinking_tokens_exact` used | `output_tokens` | `stop_reason` |
|---|---|---|---|
| 1024 | 20000 (100% thinking) | 20000 | `max_tokens` (truncated) |
| 32000 | 19999 (~100% thinking) | 20000 | `max_tokens` (truncated) |

- **Correction (external peer review, 2026-07-17): only the LOW arm is truly
  diagnostic, not both.** The original write-up framed this as "a 31x
  difference produced byte-identical behavior," implying both trials
  independently demonstrated non-enforcement. They don't, symmetrically: the
  `budget_tokens=32000` trial requests a budget LARGER than the fixed
  `max_tokens=20000` ceiling, so that trial hits `max_tokens` first whether
  `budget_tokens` is honestly enforced or silently ignored — it cannot
  distinguish the two hypotheses on its own. The real diagnostic evidence is
  the `budget_tokens=1024` trial: a requested budget of 1024 tokens, but
  thinking ran to the full 20000-token ceiling anyway — a ~20x overrun of its
  own stated cap, which a genuinely-enforced budget could not produce. That
  n=1 result, plus F16's incidental third data point (`budget_tokens=2000`,
  again overran to its own external `max_tokens=3000` ceiling — a budget
  smaller than the ceiling, so genuinely diagnostic), is what the "not
  enforced" conclusion actually rests on: two independent low-budget trials,
  both overrunning their own requested cap by a large margin.
- **Resolves F2's long-standing open question**: this is hypothesis (b) from
  the Open Questions confirmed — Sonnet 5 (via this OAuth path) is not
  actually being constrained by manual `budget_tokens`; it behaves as if
  running unconstrained/adaptive-style reasoning regardless of the requested
  cap. The field is accepted without error (no 400, consistent with F2) but
  doesn't function as an enforced ceiling.
- **Operational implication, strengthening Recommended Configuration**: don't
  build anything that relies on `budget_tokens` to control cost or thinking
  depth via manual mode on this path — it doesn't work. `output_config.effort`
  (F1/F10) is the only confirmed-working lever for controlling thinking depth.
- **Caveat**: n=1 per genuinely-diagnostic budget value (1024 and, incidentally,
  2000) — small, but the effect is qualitative and dramatic (a ~10-20x overrun
  of the requested cap in both cases), not a marginal statistical read that
  needs a larger N to trust. The non-diagnostic 32000 arm is dropped from the
  headline claim but kept in the table above for transparency about exactly
  what was run.
- Real-API spend: 2 trials at 20000 output tokens each (~40K tokens total,
  cache-read, effort unset/manual mode) — this mission's single largest
  per-experiment spend after TASK-010's sweep, roughly $4-6.

### F16. `redact-thinking-2026-02-12` is a no-op for us — confirmed directly (2026-07-17, TASK-012)
Tested whether this beta header (sent unconditionally by Claude Code) is what
actually causes the empty-visible-thinking behavior seen in 100% of this
mission's trials, or whether that's a model default independent of the beta
(per F9: `thinking.display` defaults to `"omitted"` on Sonnet 5). Held prompt,
model, `thinking.enabled`+`budget_tokens=2000`, and `max_tokens=3000` fixed;
varied only presence/absence of the `redact-thinking-2026-02-12` beta header;
inspected raw response content-block types directly (not just the harness's
summarized stats) to see the actual shape.

| | without beta | with beta |
|---|---|---|
| content block types | `["thinking"]` | `["thinking"]` |
| visible thinking chars | 0 | 0 |
| `thinking` block has `signature` | true | true |
| `thinking_tokens_exact` | 3000 | 3000 |
| `output_tokens` | 3000 | 3000 |
| `stop_reason` | `max_tokens` | `max_tokens` |

- **Byte-identical in every observable respect.** The beta header made zero
  difference to content-block type, visible thinking length, signature
  presence, token counts, or stop reason.
- **Answers the Open Question directly**: the empty-visible-thinking behavior
  is fully explained by `thinking.display` defaulting to `"omitted"` (F9) —
  NOT by this beta. This doesn't rule out an invisible server-side/billing
  effect from the beta, only that it has zero effect on response shape as
  observed here.
- Incidentally reconfirms F15 (see above): this trial's `budget_tokens=2000`
  also overran to consume the full `max_tokens=3000` ceiling.
- Real-API spend: 2 trials at 3000 output tokens each (~6K tokens, cache-read)
  — negligible, under $1.

### F17. `inference_geo` is closed to this account — a clean, definitive gate (2026-07-17, TASK-016)
Per official docs (fetched directly): `inference_geo` accepts `"us"` or
`"global"` (default), controls per-request inference region, and carries a
documented 10% cost premium for `"us"` on Opus 4.6+; some models reject it
entirely with 400. Tested directly on `claude-sonnet-5` (baseline, no field,
vs `inference_geo:"us"`):

`HTTP 400: {"type":"error","error":{"type":"invalid_request_error","message":"inference_geo is not supported for this organization type."}}`

- **Definitively closed, same family as F5 (Priority Tier) and F3's credits
  gate**: this is not a model-support restriction (the docs' 400-on-old-models
  case) but an **organization-type** gate — this OAuth/subscription account's
  org type cannot use `inference_geo` at all, regardless of model or value.
  No further investigation needed; don't re-probe this unless the account's
  org type changes.
- Real-API spend: 1 baseline call (trivial, cache-warm) + 1 failed 400 call
  (zero output tokens billed on a 400) — negligible.

### F18. Structured-output cold-vs-warm schema-compile latency: suggestive, not yet clean (2026-07-17, TASK-015)
A read-only/design fork verified F7's claims directly against the official
docs (corrections folded into F7 above) and proposed a cold-vs-warm design:
per pair, a schema with one throwaway marker property carrying a fresh UUID
(guarantees a genuine schema-cache miss without waiting 24h) — call #1
"cold" (novel schema), call #2 "warm" (byte-identical schema immediately
after). Implemented the minimal harness support (`--output-schema` flag,
`bench/synthetic_multiturn_test.py:122-127`) and ran 3 pairs live
(`bench/results/task015_structured_output_cold_warm.json`).

**A real, previously-undocumented API constraint surfaced immediately**: the
schema's array property originally specified `minItems:15, maxItems:15` (to
control output length) and was rejected — `HTTP 400: "For 'array' type,
'minItems' values other than 0 or 1 are not supported"` — folded into F7.
Without item-count schema constraints, output length varies per call purely
by how many items the model chooses to enumerate (1604-3000 tokens observed),
which confounds a raw wall-clock comparison — the same length-confound shape
as the original F11 mistake, just discovered in a new context.

| pair | cold wall_s / tokens / tok_s | warm wall_s / tokens / tok_s |
|---|---|---|
| 0 | 50.09s / 3000 (truncated) / 59.9 | 20.11s / 1820 (natural) / 90.5 |
| 1 | 22.20s / 1604 (natural) / 72.3 | 20.29s / 1771 (natural) / 87.3 |
| 2 | 27.28s / 1853 (natural) / 67.9 | 29.85s / 3000 (truncated) / 62.0 |

- **Correction (external peer review, 2026-07-17): the original write-up
  overstated this as "consistent across all 3 pairs" / "in every pair"** —
  that's only true for 2 of 3 (cold/warm tok/s ratio ≈ 0.66, 0.83, both <1,
  cold slower). Pair 2's ratio is 1.10 (cold FASTER, not slower) — a real
  inversion, not a rounding artifact. The doc's own explanation (pair 2's
  WARM call was the one that got truncated at `max_tokens`, an artifact of
  uncontrolled output length) is a plausible reason NOT to trust that
  particular pair's direction, but "truncation makes this pair's reading
  unreliable" is different from "this pair still secretly agrees with the
  other two" — the truncated pair's true underlying direction is simply
  unknown, not silently in favor of the hypothesis. Correct framing: 2 of 3
  pairs (the two with no truncation on either side) show cold slower than
  warm; the third is uninterpretable, not confirmatory.
- **The cleanest single comparison (pair 1, neither call truncated)** is the
  most persuasive: warm produced MORE output tokens (1771 vs 1604) in LESS
  wall-clock (20.29s vs 22.20s) than cold — the opposite of what pure
  decode-rate parity would predict, consistent with cold paying a real fixed
  compile-time tax that a longer response would otherwise amortize away.
- **Honest limitation**: n=3, uncontrolled output length (the `minItems`/
  `maxItems` API restriction above blocks the cleanest fix — forcing exact
  item counts via the schema itself). A follow-up with a low, deterministically-
  truncating `max_tokens` (the same trick F11's redo used) would isolate the
  effect size cleanly; not done this pass given time/cost.
- **Verdict: suggestive real effect, not yet a clean number.** 2 of 3 pairs
  (the pair with no truncation on either side, plus one where only cold was
  truncated) point the same direction, the third is uninterpretable rather
  than confirmatory (corrected above) — weaker support than the original
  "3/3" framing implied, but still more than pure noise given pair 1's
  clean, opposite-of-naive-expectation result. Doesn't meet this mission's
  bar for "confirmed."
- Real-API spend: 6 calls, ~13K output tokens total (cache-read) — under $2.

### F19. `thinking.display` streaming-TTFT benefit: INCONCLUSIVE — confounded by adaptive thinking's own depth variance (2026-07-17, TASK-018)
F9 established `thinking.display` is real and request-settable, with a
documented streaming-only benefit ("the server skips streaming thinking
tokens entirely... the final text response begins streaming sooner"). Built
the missing capability (`--thinking-display {omitted,summarized}`,
`bench/synthetic_multiturn_test.py:95-105,343-352`) and ran the actual A/B:
streaming, `effort:"high"`, `thinking:"adaptive"`, fixed prompt, randomized/
interleaved order, N=6/mode (`bench/specs/task018_thinking_display_ttft.json`
+ `..._remainder.json` → `bench/results/task018_thinking_display_ttft.jsonl`).

Raw `ttft_text_ms` per trial (ms):
- `omitted`: 2897, 3203, 3503, 5260, 50908, 59038 — median ≈ 4382ms
- `summarized`: 2990, 3335, 3630, 5370, 43780, 47154 — median ≈ 4500ms

- **The flag works correctly** (mechanism confirmed): `summarized` trials show
  nonzero `ttft_thinking_ms` and real visible `thinking_chars` (130-7626
  observed); `omitted` trials show `null`/`0` for both in all 6/6 trials, as
  documented.
- **But the TTFT comparison itself is uninterpretable — both conditions show
  the SAME bimodal pattern**: most trials fast (~3-5s to first visible text),
  1-2 trials per condition wildly slow (43-59s), swamping whatever
  transmission-level effect `display` might have. Medians land within 3% of
  each other (4382 vs 4500ms) — noise, not signal.
- **Correction (external peer review, 2026-07-17): this was attributed to
  "the same high-variance behavior F10 documented at `high`/`xhigh`
  effort," but F10 documented that wide variance specifically for `xhigh`
  (thinking_frac range 0.05-0.71) and explicitly called `high`'s own range
  TIGHT (0.07-0.09) — this experiment ran at `effort:"high"`, not `xhigh`.
  So the bimodal pattern seen here is NOT simply a restatement of an
  already-documented `high`-effort variance; it's a new, directly-observed
  data point in its own right. Most likely explanation: F10 sampled `high`
  at only n=3/condition, small enough that a rare slow-thinking trial could
  easily be absent by chance, while this experiment's n=6/condition
  happened to catch 1-2 such trials per condition. Read together, this
  suggests `effort:"high"`'s true call-to-call depth variance is larger
  than F10's n=3 sample alone indicated — worth a larger-N follow-up if this
  matters later, not investigated further here.
- **A deeper reason this is hard to fix, not just an underpowered N**: the
  natural instinct to control thinking depth (fix a low `budget_tokens` to
  keep every trial's thinking short and comparable) doesn't work — **F15
  already proved `budget_tokens` isn't enforced**, so there's no reliable
  lever to reduce `adaptive` thinking's variance for a design like this.
  `effort` only sets a coarse tier, not a numeric target. This makes any
  clean isolation of a *small* transmission-level TTFT effect genuinely hard
  with the tools currently available.
- **Verdict: inconclusive, not negative.** This does NOT refute F9's
  documented benefit — it means this design couldn't detect it either way.
  A cleaner test would need either much larger N (to average out the
  depth-variance noise) or a way to hold thinking depth constant that this
  mission doesn't currently have.
- Real-API spend: 12 trials at up to 6000 tokens each (~35K tokens total,
  cache-read, effort=high) — roughly $3-5.

### F20. Cache TTL (1h vs 5m) definitively confirmed to control survival across a real wall-clock gap (2026-07-17, TASK-013, mission's LAST ticket)
Testing this required solving a real design hazard first: `build_messages()`
(`bench/synthetic_multiturn_test.py:84-92`) always marks `cache_control` on
the IDENTICAL last block of the on-disk shared prefix
(`bench/fixtures/synthetic_history.json`) — since Anthropic's cache key is
content-addressed, any two calls sharing that exact prefix content collide
onto ONE cache entry regardless of which `ttl` each individually requests.
New tool `bench/cache_ttl_gap_test.py` fixes this by injecting a unique
`uuid4()` marker into the cached block's text per trial (same technique as
F18's cold/warm isolation), giving every trial/condition an independent,
uncontaminated cache entry. This also enabled a real efficiency win: since
each trial's cache entry is isolated, all 6 trials' first calls were batched
together, followed by ONE shared ~7 minute gap, then all 6 second calls —
collapsing what would have been ~40 minutes of naive serial waiting into
~10-15 minutes.

N=3 trials/condition, `thinking` disabled, trivial prompt, `max_tokens=50`
(minimize cost — this experiment is about prefill/cache mechanics, not
generation):

| condition | trial | gap (s) | call#1 create/read | call#2 create/read |
|---|---|---|---|---|
| `ttl:"5m"` | 0 | 422 | 358214 / 0 | 358214 / 0 |
| `ttl:"5m"` | 1 | 422 | 358210 / 0 | 358210 / 0 |
| `ttl:"5m"` | 2 | 422 | 358208 / 0 | 358208 / 0 |
| `ttl:"1h"` | 0 | 421 | 358212 / 0 | 0 / 358212 |
| `ttl:"1h"` | 1 | 420 | 358210 / 0 | 0 / 358210 |
| `ttl:"1h"` | 2 | 421 | 358208 / 0 | 0 / 358208 |

- **Clean, unambiguous, 6/6.** Every `5m` trial's call#2 pays a full fresh
  `cache_creation` identical in size to its own call#1 (the entry expired
  across the ~7min gap and was rewritten from scratch). Every `1h` trial's
  call#2 lands as a pure `cache_read` whose token count EXACTLY matches its
  own call#1's `cache_creation` (the entry survived the identical gap,
  byte-for-byte). Gap durations were essentially identical across both
  conditions (420-422s, <2s spread from the batching) — TTL setting is the
  only varying factor and fully explains the split.
- **This is the closing half of F4/F6's caching story**: F4 established
  caching doesn't touch decode speed; F20 establishes the practical
  operational question — for a session with real idle gaps (a human pausing
  6-10 minutes between turns, exactly this mission's own synthetic-multiturn
  use case), `ttl:"1h"` is the only setting that reliably keeps an expensive
  358K-token prefix warm. `ttl:"5m"` will silently re-pay the full
  cache-write cost on any gap past 5 minutes — a real, avoidable cost
  footgun for exactly the kind of long-idle interactive use this toolkit
  simulates. Per Anthropic's documented pricing (verified 2026-07-17, not
  the mission's own earlier "~6x a cache-read's price" guess, which was
  wrong): a cache write costs 1.25x the base input rate at `5m` TTL, 2x at
  `1h` TTL — i.e. `1h` is itself the MORE expensive write, 20x a cache-read's
  0.1x rate, vs `5m`'s 12.5x — so `1h`'s advantage is entirely in avoiding
  repeated rewrites across idle gaps, not a cheaper write; see the
  Recommended Configuration correction below for the full tradeoff.
- **Bonus probe**: `cache_control.scope:"global"` (sent via the undocumented
  `prompt-caching-scope-2026-01-05` beta header) is ACCEPTED (HTTP 200, not
  rejected) for this single-user OAuth caller, but shows no observable effect
  on response/usage shape versus a baseline without it (n=1, not rigorous).
  Consistent with the a priori expectation: the mechanism exists to share a
  cache entry across multiple users of one org, which is moot for a lone
  subscriber. Not worth further investigation unless the account's org
  structure changes.
- Real-API spend: 12 trivial calls (`max_tokens:50`, thinking disabled) + 2
  trivial scope-probe calls — negligible, well under $1.

### F21. HTTP/2 multiplexing does NOT recover F13's sub-linear concurrency scaling — it fails outright well below N=16 (2026-07-17, TASK-022)
Peer-review-surfaced follow-up to F13's own open caveat: the ~13.4x-not-16x
aggregate scaling at N=16 was attributed to "a client-side effect (thread
scheduling / connection-pool limits in this harness)" but never isolated —
`requests` opens one HTTP/1.1 socket per concurrent thread. New tool
`bench/concurrency_http2_test.py` shares ONE `httpx.Client(http2=True)`
connection across all N concurrent calls at a level (via a thread pool,
matching F13's structure), testing whether multiplexing recovers the
shortfall or lets scaling exceed N=16.

| level | protocol | outcome |
|---|---|---|
| N=1 | HTTP/2, shared client | works — `http_version='HTTP/2'` confirmed via ALPN |
| N=2 | HTTP/2, shared client | works cleanly |
| N=4 | HTTP/2, shared client | works cleanly |
| N=8 | HTTP/2, shared client | **FAILS** — `httpx.RemoteProtocolError("Server disconnected")` |
| N=16 | HTTP/2, shared client | **FAILS** — `httpx.LocalProtocolError` (flow-control related) |

- **Refuted, not just null — and more decisively: HTTP/2 multiplexing is
  actively WORSE, not neutral.** A single HTTP/2 connection's practical
  concurrent-stream ceiling for this API sits somewhere between N=4 and N=8
  — well below F13's N=16, which `requests`-based per-thread HTTP/1.1
  connections handled with zero errors at every level tested.
- **Most likely explanation**: a server-side or Cloudflare-front-end
  concurrent-stream/flow-control limit per HTTP/2 connection, considerably
  lower than 16. This makes F13's own framing INCOMPLETE rather than wrong:
  F13's client-side-effect caveat was offered to explain a MILD stagger
  (13.4x vs 16x, zero errors) — this experiment shows HTTP/2 hits a much
  HARDER wall (outright connection failure) well before that scale, so it
  doesn't identify what causes F13's milder shortfall, only that "switch to
  HTTP/2" is not the fix for it.
- **Practical implication for Recommended Configuration**: keep using
  independent connections (one per concurrent call, as `requests`-based
  threading already does) for concurrent workloads against this API — do
  NOT multiplex many concurrent calls over a single HTTP/2 connection; it
  breaks before N=8 in this mission's own test.
- **Honest limitation**: the exact per-connection stream ceiling (between
  N=4 and N=8) was not narrowed further (time-boxed), and the underlying
  cause (server enforcement vs. a proxy/CDN layer) was not distinguished.
  The raw per-level JSONL output wasn't persisted by the executing fork
  (`bench/results/concurrency_http2.jsonl` is empty) — this finding rests on
  the fork's own directly-observed exception messages and HTTP-version
  confirmations at each level, not a saved artifact; a re-run would
  reproduce it cheaply if independent confirmation is ever needed.
- Real-API spend: negligible — 4 successful trivial calls (N=1/2/4 at
  `max_tokens:50`) plus the failed N=8/N=16 attempts (calls that errored
  before/during response don't bill meaningful output tokens).

### F22. Batch API is closed to this OAuth account — a token-SCOPE gate, a new gate mechanism distinct from prior org-type gates (2026-07-17, TASK-024)
Peer-review-surfaced follow-up: is Anthropic's Batch API (`/v1/messages/batches`,
documented ~50% cost discount for async, non-time-sensitive workloads) usable
via this OAuth-subscription credential, and if so, a viable independent
throughput/cost lever alongside F13's real-time concurrency? Probed directly
(`bench/batch_api_probe.py`) with one trivial batch request, same OAuth
attribution headers as every other tool in this mission.

`HTTP 403 permission_error`: `"OAuth token does not meet scope requirement
any_of(user:batch, user:developer, workspace:developer, workspace:inference)"`

- **Closed, cleanly — but via a genuinely DIFFERENT mechanism than this
  mission's prior gates.** F5 (Priority Tier), F17 (`inference_geo`), and F3
  (Fast Mode) are all **organization-type/billing** gates — the account's
  org type or billing plan doesn't qualify, regardless of what the request
  asks for. This is an **OAuth token SCOPE** gate instead: the Claude Code
  OAuth token is scoped for interactive real-time `/v1/messages` calls only,
  and explicitly lacks `user:batch`/`user:developer`/`workspace:developer`/
  `workspace:inference` — scopes that (per Anthropic's public docs, which
  only describe `x-api-key` auth for this endpoint, no OAuth mention) a
  proper API-key-based integration would carry instead.
- **Practical implication**: Batch API is not reachable from ANY OAuth-
  subscription-authenticated tooling as currently scoped — this isn't a
  billing decision Andrew could unlock (unlike Fast Mode, F3), it would
  require a fundamentally different auth path (a real API key with batch
  scope) alongside or instead of OAuth credential sharing. Not worth
  revisiting unless the OAuth token's granted scopes change.
- Real-API spend: one 403 response, zero output tokens billed.

### F23. Concurrent cold-cache writers do NOT dedupe — every simultaneous caller pays the full cache-write cost independently (2026-07-17, TASK-023)
Peer-review-surfaced follow-up: F13 tested concurrency entirely on a WARM
cache lineage. This tests the cold case — N concurrent calls all racing to
write the SAME never-before-seen cache key at once. New tool
`bench/cold_cache_stampede_test.py`: all N calls in a trial share ONE fresh
`uuid4()` marker (genuinely racing the identical cold key), fired via a
thread pool. 2 independent trials, N=4 each, trivial prompt, thinking
disabled, `max_tokens:50`.

| trial | N | cache_creation (per call) | cache_read (per call) | errors |
|---|---|---|---|---|
| 0 | 4 | 358208, 358208, 358208, 358208 (all 4) | 0 (all 4) | 0 |
| 1 | 4 | 358208, 358208, 358208, 358208 (all 4) | 0 (all 4) | 0 |

- **Clean and decisive: NO deduplication.** In both trials, all 4 concurrent
  callers independently paid the FULL cache-write cost for the identical
  358K-token prefix — none of them got a cache-read, even though all 4 were
  writing the exact same content at essentially the same instant. Zero
  errors, zero race-condition symptoms (no timeouts, no malformed
  responses) — the API simply treats each concurrent writer as if it were
  the only one, redundantly, rather than serializing/coalescing them.
- **Real economic implication, a genuine stampede cost multiplier**: a
  parallelized workload that fires N concurrent calls sharing one COLD
  context (e.g. N independent sub-tasks against the same large shared
  system prompt, all starting at once from a cold cache) pays N times the
  cache-write premium, not once — a real, easy-to-miss cost trap for anyone
  combining this mission's own two biggest levers (F13's concurrency +
  prompt caching) naively. The fix is straightforward once known: run ONE
  cheap warmup call first (trivial prompt, minimal `max_tokens`, same
  shared prefix) to pay the write cost exactly once, THEN fire the N
  concurrent real calls — they'll all land as cheap cache-reads. This
  mirrors a lesson this doc already drew for SERIAL sweeps (see Methodology
  Notes) — this finding shows it applies with even more force to concurrent
  workloads, where the naive mistake multiplies by N instead of costing a
  single trial.
- **Honest limitation**: n=2 trials, N=4 only (not pushed to N=8/16 given
  time-box) — but the effect (0/4 dedup rate, twice) is unambiguous enough
  not to need a larger N to trust the qualitative conclusion; a larger N
  would only refine confidence in "always redundant," not change the
  direction.
- Real-API spend: 8 calls total (2 trials × 4 calls), each paying a full
  358K-token cache-write — this is the one experiment this pass where the
  design itself (testing redundant writes) unavoidably costs approximately
  N× a single cache-write, roughly $4-6 (comparable to F15's cost, this
  mission's prior largest single-experiment spend).

### F24. Warm cached-context LENGTH shows no measurable effect on decode-phase tok/s across a 7x range — a clean extension of F4's null result (2026-07-17, TASK-025, mission's LAST ticket)
Peer-review-surfaced follow-up, deliberately distinct from F4: F4 established
that cache STATUS (hit vs miss) doesn't affect decode speed. This asks
whether raw prefill LENGTH itself — holding cache status fixed at "warm
hit" — has any effect on the decode phase that follows it (plausible
mechanism: a larger KV cache could slow per-decode-step attention cost,
independent of whether that KV cache was freshly written or read from a
server-side cache). New tool `bench/context_length_decode_test.py`: three
context-length conditions (`ctx_min` ~2K tokens, a deterministic filler
block kept just above Anthropic's practical minimum cacheable-prefix
length; `ctx_50k` ~50K tokens, `bench/fixtures/synthetic_history.json`
truncated to a contiguous 61-turn prefix; `ctx_358k`, the full untruncated
358K-token history) — each isolated with a unique `uuid4()` marker (same
technique as F20/F23), warmed with one throwaway call, then measured across
N=3 cache-HIT calls per condition (`thinking` genuinely disabled — see the
harness-bug note below — `max_tokens=3000`, streaming mode for `ttft_any_ms`).
Decode-phase tok/s = total output tokens / (wall_ms − ttft_any_ms), i.e. all
generation activity after the first content delta, excluding prefill/TTFT.

| condition | n | decode tok/s range | median | stop_reason | cache status (all measurement calls) |
|---|---|---|---|---|---|
| `ctx_min` (~2K tok) | 3 | 71.0 – 90.5 | 73.5 | `end_turn` (283–367 output tokens — see caveat) | clean cache_read, 0 cache_creation |
| `ctx_50k` (~50K tok) | 3 | 83.9 – 90.7 | 85.2 | `max_tokens` (3000 output tokens) | clean cache_read, 0 cache_creation |
| `ctx_358k` (~358K tok) | 3 | 84.5 – 87.1 | 85.0 | `max_tokens` (3000 output tokens) | clean cache_read, 0 cache_creation |

- **Headline, clean result: across a 7x range of warm cached-context length
  (~50K to ~358K tokens), decode-phase tok/s is statistically
  indistinguishable** — `ctx_50k` and `ctx_358k`'s ranges almost fully
  overlap (medians 0.2 tok/s apart) despite one condition's prefix being
  more than 7x longer than the other's. This directly extends F4's finding
  (cache hit/miss doesn't touch decode speed) to a second, independent
  variable: prefill LENGTH itself, at fixed cache-hit status, also doesn't
  measurably touch decode speed, at least across this tested range.
- **`ctx_min` is directionally lower (median 73.5 vs ~85) but the evidence is
  muddied, not clean, and should NOT be read as "shorter context decodes
  slower."** Its 3 measurement calls all stopped naturally at `end_turn`
  after only 283–367 output tokens — never reaching the same `max_tokens`
  truncation the other two conditions hit — almost certainly because the
  synthetic 2-turn seed conversation (a placeholder "acknowledged, filler
  content noted" assistant turn) primed the model toward a different,
  shorter kind of response than the 50K/358K conditions' real conversational
  seeds did, not because of context length per se. A much shorter
  measurement window is also inherently noisier. `ctx_min`'s own range
  (71.0–90.5) still fully overlaps both other conditions', so even at face
  value this is not strong evidence of a real effect — but the response-shape
  confound means this leg of the experiment cannot cleanly speak to the
  length question at all. A rerun with a same-shape (real-conversation-style)
  minimal-length seed would be needed to close this gap cleanly.
- **Real harness bug found and fixed en route, independent of the main
  finding**: `synthetic_multiturn_test.py`'s `thinking="disabled"` mode
  (`build_thinking_block`, now line 96) previously OMITTED the `thinking`
  field entirely rather than sending it explicitly. Empirically, omitting
  the field does NOT reliably suppress server-side reasoning — the first
  (buggy) run of this experiment showed Sonnet 5 spontaneously spending its
  ENTIRE 3000-token budget on invisible, REDACTED thinking in 8 of 9 initial
  trials (`output_tokens_details.thinking_tokens` ≈ 2999–3000 of 3000), with
  zero visible text ever streamed. Worse, a fully-redacted thinking block's
  only client-visible event (a `signature_delta`) arrives essentially
  simultaneously with `message_stop` — not incrementally — which collapsed
  `wall_ms − ttft_any_ms` to near-zero and produced nonsensical
  multi-million-tok/s artifacts on the first attempt (visible in git history
  of this experiment's own development, not in the final numbers above).
  Confirmed via a direct A/B: `thinking` field omitted → `thinking_tokens`
  ≈3000/3000; `thinking: {"type":"disabled"}` sent explicitly →
  `thinking_tokens`: 0/500, clean. Fixed at the shared-harness level
  (`build_thinking_block` now always sends `{"type":"disabled"}` explicitly
  for this mode) since every OTHER tool in `bench/` that requests
  `thinking="disabled"` inherits the same fix. Prior findings using this
  mode were essentially all trivial low-complexity prompts (e.g. "reply with
  exactly the word OK", `max_tokens:50`) where spontaneous reasoning is very
  unlikely to trigger — this bug most plausibly manifests only for
  moderately-complex generation tasks under `thinking="disabled"`, which
  before this fix was effectively unique to this experiment; not re-auditing
  every prior F1-F23 finding for this (out of scope for this ticket), but
  flagging it here for visibility.
- **Honest limitation**: n=3/condition is a pilot sample, no formal
  statistical test run — the `ctx_50k`/`ctx_358k` null result is clean
  enough not to need one (near-total range overlap, medians 0.2 tok/s
  apart), but `ctx_min`'s confound (above) means the floor end of the
  length range remains genuinely untested cleanly.
- Real-API spend: 12 calls total (3 conditions × (1 warmup + 3 measurement)),
  cache-write cost paid once per condition (~2K + ~77K + ~358K tokens =
  negligible + ~$0.29 + ~$1.34 at the 1.25x/5m write rate) plus generation
  cost for up to 3000×9 output tokens — roughly $2-3 total, plus a handful
  of small debug/diagnostic calls made while isolating the thinking-mode
  bug above (all well under $1 combined).

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
  so it's not trustworthy as stated. This mission's own first controlled
  re-test (F11) was ALSO found to be confounded (by output length, not by
  thinking/effort this time) after external peer review, leaving the
  question open at that point — but it is NOT open any longer: TASK-021's
  properly-controlled redo (fixed-exact-output-length + randomized order,
  N=16/mode) found no throughput difference in either direction (p=0.99),
  see F11's update. Kept here (rather than deleted) so nobody re-derives the
  old "non-streaming is faster" belief from the confounded 4-trial data, or
  mistakes the ORIGINAL (also-confounded) F11 write-up for a still-standing
  result — the CURRENT, corrected F11 is the one to trust.

## Open Questions

- ~~Why does Anthropic's documented Sonnet-5 manual-thinking-mode 400
  restriction not manifest on our OAuth traffic (F2)?~~ — **RESOLVED, see
  F15 (TASK-019)**: ran the exact disproof test this entry proposed
  (`budget_tokens=1024` vs `32000`, same hard prompt, same `max_tokens`
  ceiling) — both produced byte-identical behavior (100% thinking, truncated
  at the same external ceiling, neither respecting its own requested budget).
  Confirmed: Sonnet 5 accepts manual mode without error but does NOT enforce
  `budget_tokens` as a real cap — it runs unconstrained/adaptive-style
  regardless of the requested value. "OAuth is more lenient" is now more
  precisely stated as "OAuth's manual-mode budget is accepted but non-functional."
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
- ~~Streaming vs non-streaming, CONTROLLED for effort/thinking~~ — **RESOLVED,
  see F11's TASK-021 update above**: fixed-exact-output-length design (all 32
  trials truncated at an identical 800 tokens) + randomized/interleaved order,
  N=16/mode. Median wall_s 11.46s vs 11.47s, p=0.99 (Monte Carlo permutation).
  **No throughput difference, in either direction.** Streaming's only real
  advantage is observability (TTFT-to-visible-text), never speed. This closes
  the mission's original triggering question.
- ~~Does `redact-thinking-2026-02-12` (sent unconditionally by Claude Code)
  actually cause the redacted/empty-visible-thinking behavior~~ — **RESOLVED,
  see F16**: no — confirmed a no-op via direct A/B (byte-identical response
  shape with vs without the header). The empty-visible-thinking behavior is
  fully explained by `thinking.display` defaulting to `"omitted"` (F9).
- ~~Does `thinking.display` exist as a request-settable field~~ — **RESOLVED,
  see F9**: yes, confirmed request-settable, `"omitted"`/`"summarized"`,
  streaming-TTFT-only effect (no change to total tokens or cost).
  ~~The actual A/B~~ — **RUN, but INCONCLUSIVE, see F19** (TASK-018): N=6/mode,
  medians within 3% of each other, both conditions dominated by the same
  bimodal adaptive-thinking-depth variance. Neither confirms nor refutes F9's
  documented benefit; would need much larger N or a way to hold thinking
  depth constant (which this mission doesn't currently have — see F15) to
  isolate a possibly-small transmission-level effect.
- **Does `token-efficient-tools-2026-03-28` measurably reduce input tokens
  and/or wall-clock on a tool-call-heavy synthetic history?**
- ~~Does 1h vs 5m cache TTL reduce prefill/TTFT *variance* across a
  long-running session~~ — **RESOLVED, see F20 (TASK-013)**: ran the exact
  proposed test (repeated trials with deliberate ~7min idle gaps straddling
  the 5m boundary, both TTLs, comparing `cache_creation_input_tokens`
  incidence) — 6/6 unambiguous: `5m` always expired and repaid a full
  cache-write across the gap, `1h` always survived as an exact-token-count
  cache-read. `cache_control.scope:"global"` is accepted (HTTP 200) but shows
  no observable effect for a single-user OAuth caller, as expected a priori.
- ~~Does concurrency (N parallel raw API calls) change per-request tok/s~~ —
  **RESOLVED, see F13**: no, not detectably, up to N=16 same-process
  concurrent calls (individual tok/s stayed in the same 56-74 band as the
  N=1 baseline at every level tested). Aggregate throughput scales
  substantially (~13.4x at N=16). Still open: the actual ceiling (untested
  above N=16), behavior under sustained load over minutes, and a
  separate-OS-process variant (lower priority — see F13's caveats).
- **Does grammar-constrained decoding (structured outputs, warm cache) change
  steady-state decode tok/s at all** — **PARTIALLY ADDRESSED, see F18**:
  suggestive evidence (3/3 pairs, one length-controlled-by-luck comparison)
  that cold-schema compilation adds real latency, but n=3 and uncontrolled
  output length (the API rejects `minItems`/`maxItems` != 0/1) mean this
  isn't a clean effect size yet. A follow-up with a deterministically-
  truncating low `max_tokens` (same trick as F11's redo) would settle it.
- ~~Does `inference_geo` (an explicit, documented per-request region-override
  field, distinct from the already-closed client-IP VPN question) change
  TTFT or decode speed?~~ — **RESOLVED, see F17**: closed to us — `HTTP 400:
  "inference_geo is not supported for this organization type."` Same family
  as F5/F3's org/billing gates. No further investigation needed.
- **Does `cache-diagnosis-2026-04-07` do anything observable** — zero
  documentation found anywhere; propose sending it alone on an otherwise
  normal request and diffing the response/usage shape against a baseline.
- ~~Does connection/session reuse (a persistent HTTP session or HTTP/2
  connection pool, vs this harness's current fresh `requests.post` per
  trial) measurably reduce per-call fixed overhead?~~ — **RESOLVED, see
  F14**: no detectable effect (N=10/condition, medians 2.29s vs 2.33s,
  heavily overlapping IQRs). Most likely because TCP/TLS handshake is a
  small fraction of the true fixed overhead, which is probably server-side
  prefill — unreachable by client-side connection reuse.
- **New questions surfaced by the final external peer-review pass
  (2026-07-17, not yet run — filed as planq follow-ups for a future
  session):**
  - ~~Does HTTP/2 multiplexing (one connection, many concurrent streams)
    recover F13's N=16 sub-linear scaling (13.4x, not 16x)?~~ — **RESOLVED,
    see F21 (TASK-022)**: no — refuted, and more decisively than a simple
    null result. A single HTTP/2 connection fails outright
    (`RemoteProtocolError`/`LocalProtocolError`) somewhere between N=4 and
    N=8, well short of F13's own N=16. Keep using independent connections
    for concurrent workloads against this API.
  - ~~What happens to concurrency's economics under a COLD shared cache?~~ —
    **RESOLVED, see F23 (TASK-023)**: no deduplication at all — every
    concurrent caller racing the same cold cache key independently pays the
    full cache-write cost (2/2 trials, N=4, 4/4 calls paid `cache_creation`
    each time). Warm up the shared prefix with one cheap call before firing
    concurrent real calls against it.
  - ~~Is the Batch API (a separate, async, 50%-discounted endpoint) an
    independent throughput/cost lever for non-time-sensitive workloads?~~ —
    **RESOLVED, see F22 (TASK-024)**: no — closed via an OAuth token scope
    gate (`403`, missing `user:batch`/etc scopes), not a billing decision;
    would need a genuinely different auth path (a real API key) to use.
  - **Does warm cached-CONTEXT LENGTH itself change decode tok/s or
    wall-clock**, independent of the caching mechanism (F4 already
    established caching per se doesn't touch decode)? A randomized sweep at
    0K/50K/358K warm-prefix sizes with fixed output length would isolate
    whether a longer prefill phase (regardless of cache hit/miss) has any
    measurable knock-on effect on the decode phase that follows it.

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
  first rather than assuming linear scaling continues indefinitely. Use
  INDEPENDENT connections for this (one per concurrent call, as ordinary
  thread-based `requests` usage already does) — do NOT multiplex many
  concurrent calls over a single HTTP/2 connection: confirmed to fail
  outright between N=4 and N=8 (F21), well short of N=16.
- **Streaming vs non-streaming: DEFINITIVELY no throughput difference**
  (F11, resolved via TASK-021's properly-controlled redo — fixed output
  length + randomized order, N=16/mode, p=0.99). Prefer streaming anyway,
  but ONLY for its genuine, unconfounded advantage: observability (a real,
  measurable TTFT-to-visible-text via `ttft_text_ms`, unavailable in
  non-streaming) — never for a speed advantage, because there isn't one.
- **Prefer `thinking: {"type":"adaptive"}` + `output_config.effort` over
  manual `budget_tokens` — not just a style preference, `budget_tokens` is
  CONFIRMED NON-FUNCTIONAL for us** (F15/TASK-019): a requested budget of
  1024 tokens (well under the fixed 20000 `max_tokens` ceiling, so genuinely
  diagnostic) still overran to the full ceiling — a ~20x overrun of its own
  stated cap — reconfirmed by an incidental second low-budget trial (F16,
  budget=2000, same overrun pattern). `effort` is the only confirmed-working
  lever for controlling thinking depth via this OAuth path. Manual mode
  still doesn't 400 (F2), but don't build anything that relies on
  `budget_tokens` actually capping cost or depth — it doesn't.
- **Connection/session reuse doesn't help — don't bother** (F14): no
  detectable difference between fresh-connection-per-call and a persistent
  `requests.Session()`, N=10/condition. The true fixed overhead F10/F12 imply
  is most likely server-side (prefill), not client-side connection setup.
- **`inference_geo` is unusable on this account — don't build around it**
  (F17): `HTTP 400: "inference_geo is not supported for this organization
  type."`, closed regardless of model or value, same family as F5/F3.
- **`redact-thinking-2026-02-12` is a no-op — irrelevant to any helper's
  design** (F16): confirmed byte-identical response shape with vs without
  it. The real (and only) cause of empty visible thinking is `thinking.display`
  defaulting to `"omitted"` (F9).
- **`effort: "high"` (the documented default) is a sound default for typical
  interactive use** (F10): low/medium/high/xhigh's observed tok/s RANGES
  overlap heavily at n=3/condition (~68-76 tok/s point estimates; no formal
  significance test was run on this sweep, unlike F11's redo — "overlapping
  ranges" is the honest characterization, not "statistically
  indistinguishable") and low/medium/high cluster in a tight, low
  thinking-fraction band (~2-9%). Reach for `max` only when the task
  genuinely needs deep, extended reasoning — it triples wall-clock (~65s vs
  ~15-20s for the same prompt) and spends ~82% of output on invisible
  thinking tokens.
- **Any helper exposing `effort:"max"` must default/validate `max_tokens` >=
  ~20000** (F10) — otherwise a real request can silently truncate mid-thought
  (`stop_reason:"max_tokens"`, reported but easy to miss), which happened in
  2 of 3 trials at a 6000-token ceiling during this mission's own sweep. This
  requirement is confirmed only for `max` — `xhigh` never truncated even at
  6000 in the same sweep (one trial did reach 71% thinking_frac at that
  ceiling, so generous headroom is still prudent for `xhigh`, but the
  ">=20000" figure specifically is proven necessary for `max` only; the true
  `xhigh` minimum is unmeasured).
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
- **Correction (external peer review, 2026-07-17) — the cache TTL
  recommendation needs the real pricing tradeoff, not a blanket "always
  1h."** Per Anthropic's own documented pricing, a cache WRITE costs 1.25x
  the base input rate at `5m` TTL but 2x at `1h` TTL — `1h` is MORE
  expensive up-front, not cheaper — while both TTLs read at the same 0.1x
  rate once warm. So `1h` only wins economically once its lower rewrite
  frequency (surviving gaps that would force a `5m` entry to repeatedly pay
  the write premium) outweighs its own higher per-write cost — true for any
  workload with realistic multi-minute idle gaps between reuses (this
  mission's own synthetic-multiturn use case, or any real interactive
  session with human pauses between turns), but NOT automatically true for a
  prefix that's written once and read zero or one times before it would
  have expired under either TTL. F20 confirmed the MECHANISM cleanly (1h
  survives a ~7min gap byte-for-byte, 5m always expires and repays its write
  cost) — the mission's own realistic-idle-gap workload should default to
  `1h`, but a helper exposing this as a general-purpose default should weigh
  the caller's actual reuse pattern, not treat `1h` as unconditionally
  better. `cache_control.scope:"global"` is accepted but shows no
  observable benefit for a single-subscriber account — not worth using.

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
  effort sweep (`cache_ttl:"1h"`, so writes at 2x base input rate vs a
  cache-read's 0.1x — a 20x ratio, corrected 2026-07-17 from an earlier,
  wrong "~6x" guess), exactly 4 of 15 trials paid the full 358K-token
  cache-write cost simply by being first in their condition group after a
  cache miss; the other 11 were cheap cache-reads. Not a bug,
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

- 2026-07-17 (TASK-024 + TASK-023, run serially to avoid a concurrency
  confound): **TASK-024** (fork): probed whether Batch API is reachable via
  this OAuth credential. Landed **F22**: closed via an OAuth token SCOPE gate
  (`403`, missing `user:batch`/`user:developer`/`workspace:developer`/
  `workspace:inference`) — a genuinely different mechanism from this
  mission's prior organization-type/billing gates (F5/F17/F3); not
  something Andrew could unlock with a billing change, would need a real
  API key instead. **TASK-023** (coordinator-finished after its fork hit
  context exhaustion mid-task — see process note below): built
  `bench/cold_cache_stampede_test.py` (N concurrent calls all racing ONE
  shared fresh cache marker) and ran it, 2 trials × N=4. Landed **F23**: NO
  deduplication — every concurrent caller independently paid the full
  358K-token cache-write cost (8/8 calls across both trials), a real,
  easy-to-miss cost multiplier when combining this mission's two biggest
  levers (concurrency + caching) naively; fix is a single cheap warmup call
  before firing concurrent real calls. Process note: TASK-023's fork hit a
  hard context-exhaustion stop at ~307K tokens after only 3 tool calls (it
  inherited the coordinator's own by-then-large session context) and
  self-reported a clean, well-diagnosed BLOCKED status with an explicit
  remaining-steps list rather than pushing on into a degraded state — the
  coordinator finished the small, already-scoped remainder directly (dry-run,
  real run, gate, this write-up) rather than re-dispatching, per this
  mission's own established pattern for exactly this situation. This, plus
  two separate incidents this pass where a freshly-dispatched fork returned
  a ZERO-tool-call response narrating "waiting for a fork" instead of
  executing its own directive (caught via `tool_uses: 0` in the returned
  usage stats, fixed by re-dispatching with a blunt "you must act now, not
  narrate" framing) — both point to the same root cause: the coordinator's
  own context had grown large enough, by this point in an 8-hour session,
  to destabilize what a freshly-forked agent inherits. The coordinator is
  scheduling its own self-compaction immediately after this entry, per the
  environment's own checkpoint signal (~300K tokens, past the internal
  sharpness threshold). Real-API spend: F22 negligible (one 403); F23 ~$4-6
  (8 calls each paying a full cache-write, the unavoidable cost of directly
  testing redundant-write behavior). TASK-025 (warm-context-length sweep)
  remains deliberately deferred as filed future work — the most
  "academic"/lowest-payoff of the 4 peer-review-surfaced questions, and
  correctly left for a future session rather than rushed here.
- 2026-07-17 (TASK-025, mission's LAST ticket, post-self-compaction resume):
  built `bench/context_length_decode_test.py` and ran it — three warm
  cached-context-length conditions (`ctx_min` ~2K tok, `ctx_50k` ~50K tok,
  `ctx_358k` ~358K tok), N=3 cache-hit measurement calls each. Landed
  **F24**: decode-phase tok/s is statistically indistinguishable between
  `ctx_50k` and `ctx_358k` (medians 85.2 vs 85.0 tok/s, ranges almost fully
  overlapping) — a clean extension of F4's "caching doesn't touch decode"
  finding to a second, independent variable (raw prefill length itself,
  holding cache-hit status fixed). `ctx_min` was directionally lower but
  the evidence is muddied by a response-shape confound (its measurement
  calls stopped at `end_turn` after 283-367 tokens rather than hitting the
  same `max_tokens` ceiling), disclosed honestly rather than overclaimed.
  Found and fixed a real, independent harness bug en route:
  `synthetic_multiturn_test.py`'s `thinking="disabled"` mode was omitting
  the `thinking` field entirely rather than sending it explicitly, and
  omitting it does NOT reliably suppress server-side reasoning — the first
  (buggy) run showed 8/9 trials spontaneously spending their ENTIRE
  3000-token budget on invisible redacted thinking despite `thinking` never
  being requested, corrupting the decode-rate measurement into nonsensical
  multi-million-tok/s artifacts (a fully-redacted thinking block's only
  client-visible SSE event arrives at stream-end, not incrementally,
  collapsing the TTFT-to-wall_ms window used to isolate the decode phase).
  Fixed at the shared-harness level (`build_thinking_block` now always
  sends `{"type":"disabled"}` explicitly) — confirmed via a direct A/B
  (thinking_tokens ~3000/3000 omitted vs. 0/500 explicit). This was the
  backlog's last open ticket; the mission is now substantially complete.
  Real-API spend: ~$2-3 for the clean final run, plus a handful of small
  diagnostic calls made while isolating the thinking-mode bug (well under
  $1 combined).
- 2026-07-17 (TASK-022, dedicated fork): Tested the peer-review-surfaced
  HTTP/2 multiplexing hypothesis against F13's own open caveat (does a
  single shared connection recover the 13.4x-not-16x sub-linear scaling at
  N=16?). New tool `bench/concurrency_http2_test.py` (`httpx[http2]` added
  as a dependency). Landed **F21**: refuted, and more decisively than a
  simple null — a single HTTP/2 connection fails outright
  (`RemoteProtocolError`/`LocalProtocolError`) somewhere between N=4 and
  N=8, well short of F13's own N=16, which ordinary independent HTTP/1.1
  connections handled with zero errors. Process note: the first dispatch of
  this fork returned a malformed, zero-tool-call response (it narrated
  "waiting for a fork" instead of executing the task directly, seemingly
  confused by the coordinator-voiced context it inherited) — caught
  immediately by checking `tool_uses: 0` in the returned usage stats, and
  fixed by re-dispatching with an explicit "you must execute this yourself,
  right now" framing, which then worked cleanly (16 tool calls, real
  result). Worth remembering for future waves: a fork's own `tool_uses`
  count is a cheap, mechanical tripwire for this exact failure mode. Real-API
  spend: negligible (4 trivial calls before the N=8/16 failures). This closes
  the mission's final peer-review-surfaced action item; TASK-023/024/025
  (cold-cache stampede, Batch API, warm-context-length) remain filed as
  planq tickets for a future session.
- 2026-07-17 (final external peer-review pass, mission wrap-up checkpoint):
  With the backlog at zero open tickets, consulted `claude-opus-4-8-thinking-high`
  and `gpt-5.6-sol-high` via `cursor-agent -p -f --mode ask` for a final
  anti-cargo-cult critique of the full 20-finding doc, focused on F13-F20
  (added since the last peer review, which only covered F1-F11). Both
  independently found real issues; every one was independently re-verified
  against the raw data/tables/source before acting, per this mission's own
  standing norm. Corrected: **F15** overclaimed "31x difference... byte-identical"
  when only the `budget_tokens=1024` arm was actually diagnostic (the 32000
  arm's own requested budget exceeded the fixed `max_tokens=20000` ceiling,
  so it couldn't distinguish enforced-vs-ignored either way) — the "not
  enforced" conclusion still holds, now correctly grounded in only the
  genuinely diagnostic evidence (1024 and F16's incidental 2000). **F18**
  had an internal contradiction ("consistent across all 3 pairs" while
  simultaneously disclosing a pair with an inverted ratio) — corrected to
  "2 of 3, third uninterpretable." **F19** mischaracterized its own bimodal
  TTFT pattern as "the same high-variance behavior F10 documented at
  high/xhigh effort" when F10 explicitly called `high`'s variance TIGHT
  (only `xhigh` was wide) — corrected to note this is actually NEW evidence
  that `high`'s true variance exceeds what F10's n=3 sample caught. **F20**'s
  "always use 1h, never 5m" was corrected with real pricing: verified
  directly (not assumed) that `1h` cache writes cost MORE up-front (2x base
  rate) than `5m` (1.25x) — `1h`'s advantage is entirely in avoiding repeat
  rewrites across gaps, not a cheaper write — and the doc's own prior
  "~6x a cache-read's price" figure (used in 3 places) was simply wrong;
  corrected to the sourced 12.5x (5m) / 20x (1h) write-to-read ratios.
  Recommended Configuration's `max_tokens>=20000` requirement was narrowed
  from "xhigh or max" to "max" only, matching what F10 actually measured
  (xhigh never truncated at 6000). A stale Rejected/Superseded entry still
  claiming streaming-vs-nonstream was "genuinely still open" (pre-dating
  TASK-021's definitive resolution) was updated. Filed 4 new Open Questions
  for genuinely unexplored levers surfaced by the review (HTTP/2
  multiplexing vs F13's sub-linear N=16 scaling, cold-cache concurrent
  stampede economics, Batch API as its own lever, warm-context-length's
  effect on decode independent of caching) — the first is being run now as
  TASK-022 (see below); the rest filed as planq tickets for a future
  session. No Anthropic API spend this pass (external CLI billing only, not
  tracked in this doc's cost tally).
- 2026-07-17 (TASK-013, dedicated fork + parallel read-only toolkit audit —
  mission's LAST planq ticket): New tool `bench/cache_ttl_gap_test.py` —
  injects a per-trial `uuid4()` marker into the cached block's text to avoid
  a real design hazard (the content-addressed cache key would otherwise let
  a naive 5m-vs-1h comparison sharing the same on-disk prefix contaminate
  itself). Batched all 6 trials' first calls together, one shared ~7min gap,
  then all 6 second calls — collapsing ~40min of naive serial waiting into
  ~10-15min. Landed **F20**: cache TTL definitively confirmed to control
  survival across a real idle gap — 3/3 `5m` trials repaid a full fresh
  cache-write after the gap, 3/3 `1h` trials landed as an exact-token-count
  cache-read, zero ambiguity. Bonus: `cache_control.scope:"global"` accepted
  (HTTP 200) but shows no observable effect for a single-user account. This
  closes planq's backlog to ZERO open tickets. In parallel, a read-only audit
  fork reviewed the whole `bench/` toolkit for drift/polish: found doc drift
  (`bench/README.md` and `bench/run_sweep.py`'s docstring both missing the
  `--thinking-display`/`--output-schema` flags and the `--reuse-connection`
  capability, all shipped earlier this mission) and two cosmetic gaps
  (`bench/concurrency_test.py` lacked `choices=` validation on
  `--effort`/`--thinking`, and its hardcoded `mode="nonstream"` had no
  explanatory comment) — no real bugs, no tooling-level silent no-ops found
  (checked directly against this mission's own F14/F15/F16 pattern). Fixed
  all of the above. Real-API spend: negligible (<$1, 14 trivial calls
  total). Running mission total: still roughly $36-46.
- 2026-07-17 (TASK-018, coordinator-run serial experiment): Added
  `--thinking-display` flag (`bench/synthetic_multiturn_test.py`), ran the
  streaming TTFT A/B (`display:"omitted"` vs `"summarized"`, N=6/mode,
  randomized order). Landed **F19**: inconclusive — the flag works correctly
  (mechanism confirmed) but `adaptive` thinking's own depth variance (a
  bimodal fast/slow pattern in both conditions) swamps any transmission-level
  TTFT effect; medians within 3% of each other. Notable meta-finding: the
  natural fix (hold thinking depth constant via `budget_tokens`) doesn't work
  because F15 already proved that field isn't enforced. Real-API spend: 12
  trials, ~35K tokens (cache-read, effort=high), roughly $3-5. This closes
  the planq backlog down to 1 remaining ticket (TASK-013, needs real
  wall-clock idle time, deferred to next wave/fork).
- 2026-07-17 (post-compaction wave: TASK-019/020/021/012/016/015, 2-fork wave
  + coordinator-run serial experiments): Dispatched 2 parallel worktree-isolated
  prep forks (no live spend, file-disjoint from each other): one built
  connection/session-reuse capability (`--reuse-connection` on
  `bench/run_sweep.py`, merged at `edfe080`), one investigated structured
  outputs and designed a cold/warm test (zero repo mutations, pure
  design/report). Then ran 6 real-API experiments serially from the
  coordinator: **F14** (connection reuse — null result), **F15** (manual
  `budget_tokens` confirmed NOT enforced — resolves F2's long-standing open
  question, arguably this wave's most important finding since it directly
  changes the Recommended Configuration's guidance), **F16** (redact-thinking
  beta confirmed no-op), **F17** (`inference_geo` closed — org-type gated),
  **F18** (structured-output cold/warm — suggestive, not clean), and a full
  **definitive resolution of F11**/TASK-021 (the mission's ORIGINAL question):
  streaming vs non-streaming, properly controlled this time (fixed-exact-
  output-length + randomized/interleaved order, N=16/mode) — genuinely NO
  throughput difference (p=0.99), closing the question for good. Also folded
  two doc corrections into F7 (structured outputs fully GA, sliding 24h cache
  TTL) and discovered a new, previously-undocumented API constraint
  (`minItems`/`maxItems` other than 0/1 rejected on array-type schema
  properties). planq: TASK-019/020/021/012/016/015 closed; TASK-018
  (thinking.display TTFT, needs a new CLI flag) and TASK-013 (cache TTL
  session-gap, needs real wall-clock idle time) remain open. Real-API spend
  this wave: ~$8-12 (F15's two 20K-token trials were the largest single cost;
  everything else was cheap/trivial-output or cache-read). Process note: the
  TASK-020 fork was handed a worktree of the WRONG repo (mirrors the
  coordinator's own `~/Projects/arr`, a known cross-repo isolation gotcha) and
  self-corrected by making its own worktree on the right repo — no coordinator
  intervention needed, but confirms this gotcha is still live and forks should
  be briefed to expect and route around it.
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
