"""Centralised configuration management for Claude Code auth."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)

from .models import AnthropicOAuthConfig

__all__ = [
    "Settings",
    "ClaudeEnvironment",
    "load_settings",
]


class ClaudeEnvironment(str):
    """Supported Claude Code deployment environments."""

    PROD = "prod"
    STAGING = "staging"
    LOCAL = "local"

    @classmethod
    def normalize(cls, value: str) -> str:
        lowered = value.strip().lower()
        if lowered in {cls.PROD, "production", "default"}:
            return cls.PROD
        if lowered in {cls.STAGING, "stage"}:
            return cls.STAGING
        if lowered in {cls.LOCAL, "dev", "development"}:
            return cls.LOCAL
        raise ValueError(
            "environment must be one of 'prod', 'staging', or 'local'"
        )


_ENVIRONMENT_CONFIGS: Dict[str, AnthropicOAuthConfig] = {
    ClaudeEnvironment.LOCAL: AnthropicOAuthConfig(
        name="local",
        base_api_url="http://localhost:3000",
        token_url="http://localhost:3000/v1/oauth/token",
        roles_url="http://localhost:3000/api/oauth/claude_cli/roles",
        client_id="22422756-60c9-4084-8eb7-27705fd5cf9a",
    ),
    ClaudeEnvironment.STAGING: AnthropicOAuthConfig(
        name="staging",
        base_api_url="https://api.anthropic.com",
        token_url="https://console.anthropic.com/v1/oauth/token",
        roles_url="https://api.anthropic.com/api/oauth/claude_cli/roles",
        client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    ),
    ClaudeEnvironment.PROD: AnthropicOAuthConfig(
        name="prod",
        base_api_url="https://api.anthropic.com",
        token_url="https://console.anthropic.com/v1/oauth/token",
        roles_url="https://api.anthropic.com/api/oauth/claude_cli/roles",
        client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    ),
}


def _split_services(raw: str) -> Tuple[str, ...]:
    parts = [segment.strip() for segment in raw.split(os.pathsep)]
    return tuple(part for part in parts if part)


class Settings(BaseModel):
    """Runtime settings sourced from environment variables with validation."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    config_dir: Path = Field(
        default_factory=lambda: Path.home() / ".claude",
        description="Base directory used for Claude Code configuration data",
    )
    environment: Literal[ClaudeEnvironment.PROD, ClaudeEnvironment.STAGING, ClaudeEnvironment.LOCAL] = Field(
        default=ClaudeEnvironment.PROD,
        description="Which Anthropic environment to target",
    )
    use_local_oauth_flag: Optional[bool] = Field(
        default=None,
        description="Internal flag signalling the local OAuth environment",
        exclude=True,
    )
    keychain_service: Optional[str] = Field(
        default=None,
        description="Explicit macOS keychain service name",
    )
    keychain_services: Tuple[str, ...] = Field(
        default=(),
        description="Search list of macOS keychain service names",
    )
    refresh_margin_ms: int = Field(
        default=30 * 60 * 1000,
        description="Threshold before expiry that triggers a refresh",
        gt=0,
    )
    timeout_connect_seconds: float = Field(
        default=5.0,
        description="HTTP connect timeout in seconds",
        gt=0,
    )
    timeout_read_seconds: float = Field(
        default=20.0,
        description="HTTP read timeout in seconds",
        gt=0,
    )
    user_agent_cli: str = Field(
        default="claude-cli/2.0.8 (external, cli)",
        description="User agent applied to CLI-facing requests",
        min_length=1,
    )
    user_agent_internal: str = Field(
        default="axios/1.8.4",
        description="User agent for internal service-to-service requests",
        min_length=1,
    )

    _sources: Dict[str, str] = PrivateAttr(default_factory=dict)

    @field_validator("environment", mode="before")
    @classmethod
    def _coerce_environment(cls, value: Optional[str]) -> str:
        if value in (None, ""):
            return ClaudeEnvironment.PROD
        try:
            return ClaudeEnvironment.normalize(str(value))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("use_local_oauth_flag", mode="before")
    @classmethod
    def _parse_bool(cls, value: Optional[str]) -> Optional[bool]:
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError(
            "USE_LOCAL_OAUTH values must be truthy/falsey strings (true/false, 1/0, yes/no, on/off)"
        )

    @field_validator("keychain_service", mode="before")
    @classmethod
    def _clean_service(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        return str(value).strip()

    @field_validator("keychain_services", mode="before")
    @classmethod
    def _clean_services(cls, value) -> Tuple[str, ...]:  # type: ignore[override]
        if value in (None, ""):
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(str(item).strip() for item in value if str(item).strip())
        if isinstance(value, str):
            return _split_services(value)
        raise TypeError("keychain services must be provided as a string or sequence")

    @model_validator(mode="after")
    def _apply_local_override(self, info: ValidationInfo):
        sources = info.context.get("sources") if info.context else {}
        environment_set = bool(sources and "environment" in sources)
        local_flag_source = sources.get("use_local_oauth_flag") if sources else None
        if self.use_local_oauth_flag and not environment_set:
            object.__setattr__(self, "environment", ClaudeEnvironment.LOCAL)
            if local_flag_source:
                self._sources["environment"] = f"{local_flag_source} (derived)"
        return self

    @model_validator(mode="after")
    def _record_sources(self, info: ValidationInfo):
        if info.context:
            provided = info.context.get("sources") or {}
            self._sources.update({k: v for k, v in provided.items() if k != "use_local_oauth_flag"})
        return self

    @computed_field(return_type=Tuple[float, float])
    @property
    def request_timeout(self) -> Tuple[float, float]:
        """Return the `(connect, read)` timeout pair for HTTP requests."""

        return (float(self.timeout_connect_seconds), float(self.timeout_read_seconds))

    @computed_field(return_type=AnthropicOAuthConfig)
    @property
    def anthropic(self) -> AnthropicOAuthConfig:
        """Return the Anthropic OAuth endpoints for the configured environment."""

        return _ENVIRONMENT_CONFIGS[self.environment]

    def source_for(self, field_name: str) -> Optional[str]:
        """Return the environment variable that supplied a field, if any."""

        return self._sources.get(field_name)

    @property
    def config_dir_overridden(self) -> bool:
        """Return True when the config directory was provided via environment."""

        default_dir = Path.home() / ".claude"
        return "config_dir" in self._sources or self.config_dir != default_dir


_FIELD_ENV_VARS: Dict[str, str] = {
    "config_dir": "CLAUDE_CODE_CONFIG_DIR",
    "environment": "CLAUDE_CODE_ENVIRONMENT",
    "use_local_oauth_flag": "CLAUDE_CODE_USE_LOCAL_OAUTH",
    "keychain_service": "CLAUDE_CODE_KEYCHAIN_SERVICE",
    "keychain_services": "CLAUDE_CODE_KEYCHAIN_SERVICES",
    "refresh_margin_ms": "CLAUDE_CODE_REFRESH_MARGIN_MS",
    "timeout_connect_seconds": "CLAUDE_CODE_TIMEOUT_CONNECT",
    "timeout_read_seconds": "CLAUDE_CODE_TIMEOUT_READ",
    "user_agent_cli": "CLAUDE_CODE_USER_AGENT_CLI",
    "user_agent_internal": "CLAUDE_CODE_USER_AGENT_INTERNAL",
}


def _gather_env(overrides: Optional[Dict[str, str]] = None) -> tuple[Dict[str, str], Dict[str, str]]:
    """Collect environment values for known settings and track their sources."""

    env = overrides or os.environ
    data: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    for field, var_name in _FIELD_ENV_VARS.items():
        raw = env.get(var_name)
        if raw is None or raw == "":
            continue
        data[field] = raw
        sources[field] = var_name
    return data, sources


def load_settings(*, env: Optional[Dict[str, str]] = None) -> Settings:
    """Return a validated `Settings` instance derived from the environment."""

    raw, sources = _gather_env(env)
    return Settings.model_validate(raw, context={"sources": sources})
