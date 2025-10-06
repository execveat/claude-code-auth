"""Filesystem lock helper matching Claude Code's directory semantics."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional, Type

from .exceptions import RefreshError
from .utils import lock_dir_for

__all__ = ["ConfigLock"]


class ConfigLock:
    """Context manager that mirrors Claude Code's filesystem lock semantics."""

    def __init__(self, config_dir: Path, stale_seconds: int = 10, poll_interval: float = 0.2) -> None:
        self._config_dir = config_dir
        self._lock_dir = lock_dir_for(config_dir)
        self._stale_seconds = stale_seconds
        self._poll_interval = poll_interval

    def __enter__(self) -> "ConfigLock":
        """Create the lock directory, waiting for existing locks to clear."""

        deadline = time.time() + max(self._stale_seconds * 3, 30)
        while True:
            try:
                self._lock_dir.mkdir()
                break
            except FileExistsError:
                now = time.time()
                try:
                    mtime = self._lock_dir.stat().st_mtime
                except FileNotFoundError:
                    continue
                if now - mtime > self._stale_seconds:
                    try:
                        self._lock_dir.rmdir()
                    except OSError:
                        pass
                elif now > deadline:
                    raise RefreshError(
                        f"Timed out waiting for Claude Code credential lock at {self._lock_dir}"
                    )
                time.sleep(self._poll_interval)
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb,
    ) -> None:
        """Release the lock directory regardless of success or failure."""

        try:
            os.utime(self._lock_dir, None)
        except OSError:
            pass
        try:
            self._lock_dir.rmdir()
        except FileNotFoundError:
            pass
