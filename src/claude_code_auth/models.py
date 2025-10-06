"""Data models for Claude Code OAuth integration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional, Tuple

__all__ = [
    "OAuthTokens",
    "AnthropicOAuthConfig",
]


@dataclass(frozen=True)
class OAuthTokens:
    """Represents Claude Code's stored OAuth credentials."""

    access_token: str
    refresh_token: Optional[str]
    expires_at_ms: Optional[int]
    scopes: Tuple[str, ...]
    subscription_type: Optional[str]

    def is_expired(self, margin_ms: int) -> bool:
        """Return True when the token is within `margin_ms` of expiry."""

        if self.expires_at_ms is None:
            return False
        return time.time() * 1000 >= self.expires_at_ms - margin_ms

    def to_store_payload(self) -> dict[str, Any]:
        """Return a Claude Code-compatible payload for persistence."""

        return {
            "accessToken": self.access_token,
            "refreshToken": self.refresh_token,
            "expiresAt": self.expires_at_ms,
            "scopes": list(self.scopes),
            "subscriptionType": self.subscription_type,
        }


@dataclass(frozen=True)
class AnthropicOAuthConfig:
    """Anthropic endpoints and constants mirrored from Claude Code."""

    name: str
    base_api_url: str
    token_url: str
    roles_url: str
    client_id: str
