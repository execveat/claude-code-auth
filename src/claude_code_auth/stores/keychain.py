"""macOS keychain-backed credential store."""

from __future__ import annotations

import json
import logging
import platform
from typing import List, Optional

from ..exceptions import CredentialUnavailableError
from ..keychain import (
    discover_keychain_services,
    read_keychain_entry,
    write_keychain_entry,
)
from ..models import OAuthTokens
from ..parsing import parse_payload
from ..settings import Settings, load_settings
from ..utils import hash_suffix_if_needed
from .base import CredentialStore

KEYCHAIN_PREFIX = "Claude Code"
KEYCHAIN_SUFFIXES = ("", "-local-oauth", "-staging-oauth")

__all__ = ["KeychainStore"]


class KeychainStore(CredentialStore):
    """Credential store backed by the macOS login keychain."""

    kind = "keychain"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or load_settings()
        self._config_dir = self._settings.config_dir
        self._logger = logging.getLogger("claude_code_auth")
        self._last_service: Optional[str] = None
        self._override_locked = False
        self._services = self._initial_services()
        self._unknown_services: Optional[List[str]] = None
        self._discovery_performed = False

    def load(self) -> Optional[OAuthTokens]:
        """Load tokens from the first matching keychain entry."""

        if platform.system() != "Darwin":
            return None
        errors: List[str] = []
        tokens = self._load_from_services(self._services, errors)
        if tokens:
            return tokens

        new_services = self._discover_additional_services()
        if new_services:
            tokens = self._load_from_services(new_services, errors)
            if tokens:
                return tokens
        if errors:
            self._logger.debug("Keychain lookups attempted: %s", "; ".join(errors))
        if self._unknown_services:
            joined = ", ".join(self._unknown_services)
            override_hint = "Set CLAUDE_CODE_KEYCHAIN_SERVICE to pick one."
            raise CredentialUnavailableError(
                "No keychain credentials found. Detected additional Claude Code "
                f"items: {joined}. {override_hint}"
            )
        return None

    def save(self, tokens: OAuthTokens) -> None:
        """Persist tokens back to the keychain entry that succeeded."""

        if platform.system() != "Darwin":
            raise CredentialUnavailableError("Keychain not available on this platform")
        if not self._last_service:
            raise CredentialUnavailableError(
                "No keychain entry has been loaded yet; call load() first"
            )
        payload = json.dumps({"claudeAiOauth": tokens.to_store_payload()})
        write_keychain_entry(self._last_service, payload)

    def _discover_additional_services(self) -> List[str]:
        """Return newly found keychain service names, caching the result."""

        if self._discovery_performed or self._override_locked:
            # Environment overrides short-circuit discovery; reruns just reuse previous results.
            self._discovery_performed = True
            self._unknown_services = self._unknown_services or []
            return []

        self._discovery_performed = True
        discovered = discover_keychain_services()
        extras: List[str] = []
        seen = set(self._services)
        for service in discovered:
            if not service.startswith(KEYCHAIN_PREFIX):
                continue
            if service in seen:
                continue
            self._services.append(service)
            extras.append(service)
            seen.add(service)
        self._unknown_services = extras
        return extras

    def _load_from_services(
        self, services: List[str], errors: List[str]
    ) -> Optional[OAuthTokens]:
        """Attempt to load tokens from the provided service names."""

        for service in services:
            payload, err = read_keychain_entry(service)
            if payload is None:
                if err:
                    errors.append(f"{service}: {err.strip()}")
                continue
            tokens = parse_payload(payload)
            if tokens:
                self._last_service = service
                return tokens
        return None

    def _initial_services(self) -> List[str]:
        """Compute the baseline list of services to inspect."""

        override_list = self._env_list_override()
        if override_list is not None:
            self._override_locked = True
            return override_list

        explicit = self._explicit_override()
        if explicit is not None:
            self._override_locked = True
            return [explicit]

        self._override_locked = False
        return self._expected_candidates()

    def _env_list_override(self) -> Optional[List[str]]:
        """Return an explicit list override from settings, if provided."""

        services = self._settings.keychain_services
        if not services:
            return None
        return list(services)

    def _explicit_override(self) -> Optional[str]:
        """Return a single override from settings, if set."""

        value = self._settings.keychain_service
        if not value:
            return None
        return value

    def _expected_candidates(self) -> List[str]:
        """Derive the default Claude Code service names for this config dir."""

        hash_suffix = hash_suffix_if_needed(self._config_dir, self._settings)
        expected: List[str] = []
        for suffix in KEYCHAIN_SUFFIXES:
            expected.append(f"{KEYCHAIN_PREFIX}{suffix}-credentials{hash_suffix}")
        for suffix in KEYCHAIN_SUFFIXES:
            expected.append(f"{KEYCHAIN_PREFIX}{suffix}{hash_suffix}")
        return expected
