"""Utilities for sharing Claude Code OAuth credentials with companion tools."""

from __future__ import annotations

from .exceptions import (
    ClaudeCodeAuthError,
    CredentialUnavailableError,
    KeychainAccessError,
    RefreshError,
)
from .fingerprint import (
    build_attribution_header_text,
    build_attribution_system_blocks,
    compute_fingerprint,
)
from .manager import ClaudeCodeOAuthManager, default_manager
from .models import AnthropicOAuthConfig, OAuthTokens
from .settings import Settings, load_settings

__all__ = [
    "ClaudeCodeAuthError",
    "CredentialUnavailableError",
    "KeychainAccessError",
    "RefreshError",
    "OAuthTokens",
    "AnthropicOAuthConfig",
    "ClaudeCodeOAuthManager",
    "default_manager",
    "Settings",
    "load_settings",
    "compute_fingerprint",
    "build_attribution_header_text",
    "build_attribution_system_blocks",
]
