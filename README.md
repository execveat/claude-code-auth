# claude-code-auth

Reuse your Claude Code OAuth session to call the Anthropic API from Python. Does not require API keys and credits. Works with macOS Keychain or `~/.claude/.credentials.json`, refreshes tokens early, and plays nice with concurrent Claude Code processes.

## Why

- Use your Claude Code subscription for Anthropic API calls.
- No extra API key to manage.
- Matches Claude Code’s storage and refresh behavior.
- Lock-aware so multiple tools can cooperate.

## Installation

> This will be published to PyPI once the package is stable.
> Please follow development instructions below for now.

```bash
uv pip install claude-code-auth
# or
pip install claude-code-auth
```

The package targets Python 3.9+.

## Quick start

```python
import requests

from claude_code_auth import ClaudeCodeOAuthManager

manager = ClaudeCodeOAuthManager()

# Use the automatically refreshed headers with requests or httpx.
response = requests.get(
    "https://api.anthropic.com/v1/messages",
    # Always fetch the latest valid token; the manager will refresh when expiration is near.
    headers=manager.build_headers(),
    timeout=30,
    json={
      'model': 'claude-3-5-haiku-20241022',
      'max_tokens': 512,
      'messages': [
        {
          'role': 'user',
          'content': 'What should I search for to find the latest developments in renewable energy?'
        }
      ]
    }
)
response.raise_for_status()
print(response.json())
```

### Manual refresh

The manual refresh should really only be needed if a separate Claude Code process is running in the background and it happened to refresh the token before we did. If you need to force a refresh (for example after a 401 response):

```python
manager.refresh()
```

The manager re-reads the credential store inside Claude Code's lock directory before contacting Anthropic to minimise races with the Claude Code.

## macOS keychain specifics

On macOS the module mirrors the Claude Code CLI's keychain probing order:

1. `Claude Code-credentials`
2. `Claude Code-local-oauth-credentials`
3. `Claude Code-staging-oauth-credentials`
4. Any other keychain items whose service name starts with `Claude Code`

If the keychain denies access (the common `User interaction is not allowed.` error), a clear warning is emitted explaining how to unlock the keychain (`security unlock-keychain login.keychain-db` or via Keychain Access). A plaintext fallback is used only when the keychain cannot be read.

You can target a specific keychain item with:

```bash
export CLAUDE_CODE_KEYCHAIN_SERVICE="Claude Code-credentials"
# or provide a search list separated by os.pathsep
export CLAUDE_CODE_KEYCHAIN_SERVICES="Claude Code-credentials:/custom/service"
```

## Configuration

Call `load_settings()` if you need to inspect the current values programmatically or to discover which variables were used via `settings.source_for("<field>")`.

| Setting                                            | Environment variable(s)                                   | Default                            | Notes                                                                                                                                      |
| -------------------------------------------------- | --------------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `config_dir`                                       | `CLAUDE_CODE_CONFIG_DIR`                                  | `~/.claude`                        | Determines where plaintext credentials live and which keychain suffix hash is used.                                                        |
| `environment`                                      | `CLAUDE_CODE_ENVIRONMENT`                                 | `prod`                             | Accepts `prod`, `staging`, or `local`. When unset, setting `CLAUDE_CODE_USE_LOCAL_OAUTH` to a truthy value forces the `local` environment. |
| `keychain_service`                                 | `CLAUDE_CODE_KEYCHAIN_SERVICE`                            | `None`                             | Pin the macOS keychain lookup to a specific service.                                                                                       |
| `keychain_services`                                | `CLAUDE_CODE_KEYCHAIN_SERVICES`                           | `()`                               | Ordered search list split by `os.pathsep`. Locks discovery to the provided names.                                                          |
| `refresh_margin_ms`                                | `CLAUDE_CODE_REFRESH_MARGIN_MS`                           | `1_800_000`                        | Milliseconds before expiry that trigger proactive refresh; must be positive.                                                               |
| `timeout_connect_seconds` / `timeout_read_seconds` | `CLAUDE_CODE_TIMEOUT_CONNECT`, `CLAUDE_CODE_TIMEOUT_READ` | `5s` / `20s`                       | Applied to both refresh and profile lookups.                                                                                               |
| `user_agent_cli`                                   | `CLAUDE_CODE_USER_AGENT_CLI`                              | `claude-cli/2.0.8 (external, cli)` | Overrides the public-facing user agent header.                                                                                             |
| `user_agent_internal`                              | `CLAUDE_CODE_USER_AGENT_INTERNAL`                         | `axios/1.8.4`                      | Overrides the internal service-to-service user agent.                                                                                      |

Tokens stored in the plaintext file can be refreshed; when the keychain is used its winning entry is updated in place.

## Concurrency

The module honours Claude Code's lock (`~/.claude.lock`) during refreshes. If another process holds the lock the helper waits, re-validates credentials, and only then talks to Anthropic. Stale locks are cleaned up automatically after 10 seconds, mirroring the Claude Code's `proper-lockfile` defaults.

## Error handling

- `CredentialUnavailableError` – no usable credentials were found.
- `KeychainAccessError` – the macOS keychain refused access (with remediation guidance).
- `RefreshError` – the refresh endpoint returned a non-success status.

All errors are actionable and include context about the location that failed.

## Development

The project uses a standard `pyproject.toml` with Hatch build tooling and is compatible with `uv` for dependency management.

```bash
uv pip install -e .
uv run python -m unittest discover -s tests -p "test_*.py"
```

Contributions should preserve compatibility with the Claude Code's behaviour to remain a good neighbour in multi-process environments.
