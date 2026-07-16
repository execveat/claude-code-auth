"""Main entry point for consumers of the Claude Code OAuth helper."""

from __future__ import annotations

import json
import logging
import platform
import uuid
from typing import Any, List, Optional, Tuple

import requests

from .exceptions import CredentialUnavailableError, KeychainAccessError, RefreshError
from .fingerprint import build_attribution_system_blocks
from .locking import ConfigLock
from .models import OAuthTokens
from .settings import Settings, load_settings
from .stores import CredentialStore, KeychainStore, PlaintextStore
from .utils import config_dir, now_ms

__all__ = [
    "ClaudeCodeOAuthManager",
    "default_manager",
]

# Kept minimal historically; updated 2026-07 against a real captured Claude
# Code v2.1.150 request (~/Projects/obol/local/proxy-headers.log) which sends
# eleven capability tokens, not two. `claude-code-20250219` in particular is
# believed to be the flag that marks this as genuine Claude Code product
# traffic -- requests are otherwise NOT rejected for missing it alone (the
# real blocker for OAuth auth is the attribution/fingerprint system block,
# see fingerprint.py), but omitting it is still a fidelity gap worth closing.
ANTHROPIC_HEADER_BETA = (
    "claude-code-20250219,oauth-2025-04-20,context-1m-2025-08-07,"
    "interleaved-thinking-2025-05-14,context-management-2025-06-27,"
    "prompt-caching-scope-2026-01-05,advisor-tool-2026-03-01,"
    "advanced-tool-use-2025-11-20,effort-2025-11-24,extended-cache-ttl-2025-04-11,"
    "cache-diagnosis-2026-04-07"
)
ANTHROPIC_HEADER_VERSION = "2023-06-01"


class ClaudeCodeOAuthManager:
    """Loads and refreshes Claude Code OAuth credentials on demand."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        refresh_margin_ms: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialise the manager with the configured credential stores."""

        self._settings = settings or load_settings()
        self._refresh_margin_ms = (
            refresh_margin_ms
            if refresh_margin_ms is not None
            else self._settings.refresh_margin_ms
        )
        self._logger = logger or logging.getLogger("claude_code_auth")
        self._config_dir = config_dir(self._settings)
        self._endpoints = self._settings.anthropic
        self._stores: List[CredentialStore] = []
        if platform.system() == "Darwin":
            self._stores.append(KeychainStore(self._settings))
        self._stores.append(PlaintextStore(self._config_dir))
        self._active_store: Optional[CredentialStore] = None
        self._tokens: Optional[OAuthTokens] = None

    @property
    def access_token(self) -> str:
        """Return a valid access token, refreshing as needed."""

        tokens = self._ensure_tokens()
        return tokens.access_token

    @property
    def tokens(self) -> OAuthTokens:
        """Return the full token payload, triggering refresh when required."""

        return self._ensure_tokens()

    def refresh(self) -> OAuthTokens:
        """Force a refresh token flow and return updated credentials."""

        previous_tokens = self._tokens
        with ConfigLock(self._config_dir):
            tokens = self._load_tokens()
            if previous_tokens is not None and tokens != previous_tokens:
                self._tokens = tokens
                return tokens
            store = self._active_store
            if store is None or not store.writable:
                raise CredentialUnavailableError(
                    "Current credential source does not support refresh"
                )
            if not tokens.refresh_token:
                raise CredentialUnavailableError(
                    "No refresh token available; ensure Claude Code login was completed"
                )
            return self._refresh_with_store_locked(tokens, store)

    def build_headers(self) -> dict[str, str]:
        """Return HTTP headers suitable for authenticated API calls.

        Headers alone are NOT sufficient for OAuth-authenticated /v1/messages
        calls to succeed -- see build_system_blocks() and the module docs in
        fingerprint.py. Without the attribution/fingerprint system block,
        requests built from these headers get a bare 429 rate_limit_error
        with no rate-limit accounting headers at all (confirmed empirically:
        identical requests succeed instantly once that block is added).
        """

        token = self.access_token
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": self._settings.user_agent_cli,
            "anthropic-beta": ANTHROPIC_HEADER_BETA,
            "anthropic-dangerous-direct-browser-access": "true",
            "anthropic-version": ANTHROPIC_HEADER_VERSION,
            "X-App": "cli",
            "X-Claude-Code-Session-Id": str(uuid.uuid4()),
            "X-Client-Request-Id": str(uuid.uuid4()),
        }

    def build_system_blocks(
        self,
        message_text: str,
        *,
        entrypoint: str = "cli",
        include_cli_prefix: bool = True,
    ) -> list[dict]:
        """Build the required `system` array prefix for OAuth-authenticated requests.

        `message_text` must be the first user message's text content --
        the fingerprint is computed from specific character indices of it
        (see fingerprint.py). Put any additional system content AFTER these
        blocks in the request's `system` array; never reorder, merge, or
        drop them.
        """

        return build_attribution_system_blocks(
            message_text,
            version=self._settings.cli_version,
            entrypoint=entrypoint,
            include_cli_prefix=include_cli_prefix,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_tokens(self, *, force_reload: bool = False) -> OAuthTokens:
        """Ensure cached tokens are present and valid, refreshing if expired."""

        if force_reload:
            self._tokens = None
        if self._tokens is None:
            self._tokens = self._load_tokens()
        tokens = self._tokens
        if tokens.is_expired(self._refresh_margin_ms):
            store = self._active_store
            if tokens.refresh_token and store and store.writable:
                self._logger.info("Claude token nearing expiry; refreshing now")

                with ConfigLock(self._config_dir):
                    tokens = self._refresh_with_store_locked(tokens, store)
            else:
                raise CredentialUnavailableError(
                    "Access token is expiring and cannot be refreshed automatically"
                )
        return tokens

    def _load_tokens(self) -> OAuthTokens:
        """Load tokens from the first store that yields usable credentials."""

        errors: List[str] = []
        for store in self._stores:
            try:
                tokens = store.load()
            except CredentialUnavailableError as exc:
                if isinstance(exc, KeychainAccessError):
                    self._logger.warning("macOS Keychain access failed: %s", exc)
                errors.append(f"{store.describe()}: {exc}")
                continue
            if tokens:
                self._active_store = store
                self._logger.debug("Using Claude credentials from %s", store.describe())
                return tokens
        joined = "; ".join(errors) if errors else "no credential sources returned data"
        raise CredentialUnavailableError(
            f"Unable to locate Claude credentials: {joined}"
        )

    def _refresh_via_network(self, refresh_token: str) -> OAuthTokens:
        """Exchange the refresh token against Anthropic's OAuth endpoint."""

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._endpoints.client_id,
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": self._settings.user_agent_internal,
        }
        response = requests.post(
            self._endpoints.token_url,
            json=payload,
            timeout=self._settings.request_timeout,
            headers=headers,
        )
        if response.status_code != 200:
            raise RefreshError(
                f"Token refresh failed with status {response.status_code}: {response.text.strip()}"
            )
        data = response.json()
        access = data.get("access_token")
        if not access:
            raise RefreshError("Token refresh response missing 'access_token'")
        new_refresh = data.get("refresh_token", refresh_token)
        expires_in = data.get("expires_in")
        expires_at_ms = None
        if isinstance(expires_in, (int, float)):
            expires_at_ms = now_ms() + int(expires_in * 1000)
        scope_value = data.get("scope")
        scopes: Tuple[str, ...]
        if isinstance(scope_value, str):
            scopes = tuple(scope_value.split())
        else:
            scopes = ()
        subscription = self._fetch_subscription_type(access)
        return OAuthTokens(
            access_token=access,
            refresh_token=new_refresh,
            expires_at_ms=expires_at_ms,
            scopes=scopes,
            subscription_type=subscription,
        )

    def _refresh_with_store_locked(
        self,
        tokens: OAuthTokens,
        store: CredentialStore,
    ) -> OAuthTokens:
        """Refresh tokens using the supplied store while the config lock is held."""

        fresh_tokens = store.load() or tokens
        if not fresh_tokens.refresh_token:
            raise CredentialUnavailableError("Refresh token missing from storage")
        refreshed = self._refresh_via_network(fresh_tokens.refresh_token)
        store.save(refreshed)
        self._tokens = refreshed
        return refreshed

    def _fetch_profile(self, access_token: str) -> Optional[dict[str, Any]]:
        """Look up the subscription type associated with the access token."""

        url = f"{self._endpoints.base_api_url}/api/oauth/profile"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": self._settings.user_agent_internal,
        }
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=self._settings.request_timeout,
            )
        except requests.RequestException as exc:
            self._logger.debug("Profile lookup failed: %s", exc)
            return None
        if response.status_code != 200:
            self._logger.debug(
                "Profile lookup returned %s: %s",
                response.status_code,
                response.text.strip(),
            )
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            pass
        return None

    def _fetch_subscription_type(self, access_token: str) -> Optional[str]:
        """Look up the subscription type associated with the access token."""
        payload = self._fetch_profile(access_token)

        organization = payload.get("organization")
        if isinstance(organization, dict):
            value = organization.get("organization_type")
            if isinstance(value, str) and value in {"claude_team", "team"}:
                return "team"
        return None


def default_manager() -> ClaudeCodeOAuthManager:
    """Return a lazily configured default manager instance."""

    return ClaudeCodeOAuthManager()
