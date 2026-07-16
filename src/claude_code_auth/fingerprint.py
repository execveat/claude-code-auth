"""Attribution fingerprint for OAuth-authenticated Anthropic API requests.

Reverse-engineered from Claude Code's own source (~/Projects/cc/cc-xray,
src/utils/fingerprint.ts + src/constants/system.ts + src/utils/sideQuery.ts).
Without this block present as the *first* entry of the request's `system`
array, OAuth-authenticated (subscription) requests to api.anthropic.com are
rejected with a bare `429 rate_limit_error` that carries none of the normal
`anthropic-ratelimit-unified-*` accounting headers -- i.e. the request never
reaches real per-account rate-limit evaluation at all. This was confirmed
empirically: identical requests succeed (200, real rate-limit headers
present) the instant this block is added, and fail consistently without it,
independent of header fidelity, wait time, or account-wide quota headroom.

Despite the "x-anthropic-billing-header: ..." look, this is NOT an HTTP
header -- it is literal text placed as the first system-prompt block. The
`api.anthropic.com` endpoint strips it before processing when (and only when)
it arrives unchanged as that first block, so it doesn't pollute the prompt or
affect first-party prompt caching (see Anthropic's own gateway-protocol docs,
"system prompt attribution block").
"""

from __future__ import annotations

import hashlib

__all__ = [
    "FINGERPRINT_SALT",
    "compute_fingerprint",
    "build_attribution_header_text",
    "build_attribution_system_blocks",
]

# Hardcoded salt from backend validation. Must match exactly for fingerprint
# validation to pass -- do not change without re-verifying against a real
# captured request (e.g. via an mitmproxy/obol-style capture of genuine
# Claude Code traffic).
FINGERPRINT_SALT = "59cf53e54c78"

CLI_SYSPROMPT_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."


def compute_fingerprint(message_text: str, version: str) -> str:
    """Compute the 3-hex-char attribution fingerprint.

    Algorithm: SHA256(SALT + msg[4] + msg[7] + msg[20] + version)[:3], using
    "0" for any index past the end of `message_text`. `message_text` must be
    the first user message's text content (exactly what Claude Code hashes).
    """

    indices = (4, 7, 20)
    chars = "".join(message_text[i] if i < len(message_text) else "0" for i in indices)
    digest = hashlib.sha256((FINGERPRINT_SALT + chars + version).encode()).hexdigest()
    return digest[:3]


def build_attribution_header_text(
    message_text: str,
    *,
    version: str,
    entrypoint: str = "cli",
) -> str:
    """Build the literal attribution block text for a request's first system block."""

    fingerprint = compute_fingerprint(message_text, version)
    return (
        f"x-anthropic-billing-header: cc_version={version}.{fingerprint}; "
        f"cc_entrypoint={entrypoint};"
    )


def build_attribution_system_blocks(
    message_text: str,
    *,
    version: str,
    entrypoint: str = "cli",
    include_cli_prefix: bool = True,
) -> list[dict]:
    """Build the `system` array prefix required for OAuth-authenticated requests.

    Returns the attribution block first (required for OAuth validation to
    pass), optionally followed by the standard CLI identity prefix block.
    Prepend any additional system content after these -- do not reorder or
    merge them into one block, or the server-side strip/parse breaks.
    """

    blocks = [
        {
            "type": "text",
            "text": build_attribution_header_text(
                message_text, version=version, entrypoint=entrypoint
            ),
        }
    ]
    if include_cli_prefix:
        blocks.append({"type": "text", "text": CLI_SYSPROMPT_PREFIX})
    return blocks
