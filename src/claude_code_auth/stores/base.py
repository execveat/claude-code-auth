"""Abstract credential store interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..models import OAuthTokens

__all__ = ["CredentialStore"]


class CredentialStore(ABC):
    kind: str

    @abstractmethod
    def load(self) -> Optional[OAuthTokens]:  # pragma: no cover - interface only
        """Return tokens if available, otherwise None."""
        raise NotImplementedError

    @abstractmethod
    def save(self, tokens: OAuthTokens) -> None:  # pragma: no cover - interface only
        """Persist the provided tokens."""
        raise NotImplementedError

    @property
    def writable(self) -> bool:
        """Return True when `save` can be called for this store."""
        return True

    def describe(self) -> str:
        """Return a short human-readable description of the store."""
        return self.kind
