#!/usr/bin/env python3
"""One-shot probe: is Anthropic's Message Batches API (POST
/v1/messages/batches, documented 50% cost discount, async processing)
reachable with this OAuth-subscription credential, the same way
bench/synthetic_multiturn_test.py's direct /v1/messages calls are?

Anthropic's own docs describe the Batches endpoint using standard `x-api-key`
authentication and don't mention OAuth/subscription-token eligibility at all
(verified via WebSearch, 2026-07-17) -- consistent with this mission's
recurring pattern of real API features gated by organization/billing type
regardless of model (F5 Priority Tier, F17 inference_geo, F3 Fast Mode).
This probe finds out empirically rather than assuming.

Reuses ClaudeCodeOAuthManager exactly like synthetic_multiturn_test.py (same
Bearer token, same mandatory attribution/fingerprint system block -- omitting
it produces a bare 429 that looks like a quota issue but isn't).

Usage:
    uv run python bench/batch_api_probe.py
"""

import json
import sys
import uuid

import requests

from claude_code_auth import ClaudeCodeOAuthManager

BATCHES_URL = "https://api.anthropic.com/v1/messages/batches"


def main():
    manager = ClaudeCodeOAuthManager()
    headers = manager.build_headers()
    text = "Reply with exactly the word OK and nothing else."
    system = manager.build_system_blocks(text)

    body = {
        "requests": [
            {
                "custom_id": f"probe-{uuid.uuid4()}",
                "params": {
                    "model": "claude-sonnet-5",
                    "max_tokens": 16,
                    "system": system,
                    "messages": [{"role": "user", "content": text}],
                },
            }
        ]
    }

    print(f"POST {BATCHES_URL}", file=sys.stderr)
    resp = requests.post(BATCHES_URL, headers=headers, json=body, timeout=60)
    print(f"HTTP {resp.status_code}", file=sys.stderr)
    print(resp.text[:2000], file=sys.stderr)

    result = {
        "status_code": resp.status_code,
        "response_body": resp.text[:2000],
    }
    print(json.dumps(result))
    return 0 if resp.status_code < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
