"""General helpers used across the Claude Code auth package."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from .settings import Settings, load_settings

__all__ = [
    "config_dir",
    "lock_dir_for",
    "ensure_directory",
    "now_ms",
    "hash_suffix_if_needed",
]


def config_dir(settings: Settings | None = None) -> Path:
    """Return the Claude Code configuration directory from settings."""

    active_settings = settings or load_settings()
    return active_settings.config_dir


def lock_dir_for(path: Path) -> Path:
    """Return the lock directory path Claude Code expects for `path`."""

    return Path(f"{path}.lock")


def ensure_directory(path: Path) -> None:
    """Create the directory (and parents) if it does not already exist."""

    path.mkdir(parents=True, exist_ok=True)


def now_ms() -> int:
    """Return the current epoch time in milliseconds."""

    return int(time.time() * 1000)


def hash_suffix_if_needed(
    resolved_config_dir: Path, settings: Settings | None = None
) -> str:
    """Return a short hash suffix when custom config directories are active."""

    active_settings = settings or load_settings()
    if not active_settings.config_dir_overridden:
        return ""
    try:
        digest = hashlib.sha256(str(resolved_config_dir).encode("utf-8")).hexdigest()[:8]
    except Exception:  # pragma: no cover - extremely unlikely
        return ""
    return f"-{digest}"
