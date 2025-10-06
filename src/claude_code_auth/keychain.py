"""Helpers for interacting with the macOS keychain."""

from __future__ import annotations

import getpass
import re
import subprocess
from functools import cache
from typing import List, Optional, Sequence, Tuple

from .exceptions import KeychainAccessError

__all__ = [
    "discover_keychain_services",
    "read_keychain_entry",
    "write_keychain_entry",
]

@cache
def _user() -> str:
    """Return the current username, caching the lookup."""

    return getpass.getuser()


@cache
def _login_keychain_path() -> str:
    """Return the login keychain path as resolved by `security login-keychain`."""

    completed = _run_security_command(["login-keychain"], add_login_keychain=False)
    if completed.returncode != 0:
        return "login.keychain-db"
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return "login.keychain-db"
    return lines[-1].strip('"')


def _run_security_command(
    args: Sequence[str], *, add_login_keychain: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run `security` with the provided arguments and optional login keychain."""

    final_args = list(args)
    if add_login_keychain:
        final_args.append(_login_keychain_path())
    completed = subprocess.run(
        ["security", *final_args],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed


def discover_keychain_services() -> List[str]:
    """Return ordered, unique service names stored in the login keychain."""

    dump = _run_security_command(["dump-keychain"])
    if dump.returncode != 0:
        return []

    matches = re.findall(r'"svce"(?:<blob>)?="([^"]+)"', dump.stdout)
    return list(dict.fromkeys(matches))


def read_keychain_entry(service: str) -> Tuple[Optional[str], Optional[str]]:
    """Return the payload for a generic password entry."""

    completed = _run_security_command(
        ["find-generic-password", "-a", _user(), "-s", service, "-w"],
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").lower()
        if "user interaction is not allowed" in stderr:
            raise KeychainAccessError(
                "Access to the macOS Keychain was denied. Unlock the keychain via "
                "the Keychain Access app or run 'security unlock-keychain' before "
                "retrying."
            )
        return None, completed.stderr
    return completed.stdout.strip(), None


def write_keychain_entry(service: str, payload: str) -> None:
    """Persist a generic password entry for the given service."""

    completed = _run_security_command(
        [
            "add-generic-password",
            "-a",
            _user(),
            "-s",
            service,
            "-w",
            payload,
            "-U",
        ],
    )
    if completed.returncode != 0:
        raise KeychainAccessError(
            f"Failed to update keychain item '{service}': {completed.stderr.strip()}"
        )
