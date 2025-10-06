"""Package-specific exception hierarchy."""

from __future__ import annotations

__all__ = [
    "ClaudeCodeAuthError",
    "CredentialUnavailableError",
    "KeychainAccessError",
    "RefreshError",
]


class ClaudeCodeAuthError(RuntimeError):
    """Base class for errors raised by this package."""


class CredentialUnavailableError(ClaudeCodeAuthError):
    """Raised when no usable credentials can be located."""


class KeychainAccessError(CredentialUnavailableError):
    """Raised when the macOS Keychain refuses access."""


class RefreshError(ClaudeCodeAuthError):
    """Raised when token refresh fails."""
