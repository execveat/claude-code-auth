"""Plaintext credential store backed by Claude Code's JSON file."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ..exceptions import CredentialUnavailableError
from ..models import OAuthTokens
from ..parsing import parse_payload
from ..utils import ensure_directory
from .base import CredentialStore

__all__ = ["PlaintextStore"]


class PlaintextStore(CredentialStore):
    """Reads and writes directly to Claude Code's JSON credential file."""

    kind = "plaintext"

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._path = config_dir / ".credentials.json"

    def load(self) -> Optional[OAuthTokens]:
        """Return stored tokens from the JSON credential file, if any."""

        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CredentialUnavailableError(
                f"Failed to parse {self._path}: {exc}"
            ) from exc
        return parse_payload(data)

    def save(self, tokens: OAuthTokens) -> None:
        """Persist tokens in the JSON file with Claude Code's layout."""

        ensure_directory(self._config_dir)
        payload = tokens.to_store_payload()
        data: dict[str, Any] = {}
        if self._path.exists():
            try:
                existing = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    data = existing
            except json.JSONDecodeError:
                data = {}
        data["claudeAiOauth"] = payload
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self._path)
