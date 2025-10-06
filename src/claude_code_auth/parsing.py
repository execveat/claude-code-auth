"""Token payload parsing helpers."""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator

from .models import OAuthTokens

__all__ = ["parse_payload"]


class _RawOAuthPayload(BaseModel):
    """Internal schema mirroring Claude Code's stored token JSON."""

    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(alias=AliasChoices("accessToken", "access_token"))
    refresh_token: Optional[str] = Field(
        default=None, alias=AliasChoices("refreshToken", "refresh_token")
    )
    expires_at_ms: Optional[int] = Field(
        default=None, alias=AliasChoices("expiresAt", "expires_at")
    )
    scopes: tuple[str, ...] = Field(
        default=(), alias=AliasChoices("scopes", "scope")
    )
    subscription_type: Optional[str] = Field(
        default=None,
        alias=AliasChoices("subscriptionType", "subscription_type"),
    )

    @field_validator("refresh_token", "subscription_type", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("expires_at_ms", mode="before")
    @classmethod
    def _coerce_expiry(cls, value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @field_validator("scopes", mode="before")
    @classmethod
    def _normalize_scopes(cls, value: Any) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return tuple(scope for scope in value.split() if scope)
        try:
            return tuple(str(scope) for scope in value if scope)
        except TypeError:
            return ()


def parse_payload(raw: Any) -> Optional[OAuthTokens]:
    """Parse arbitrary payload data into `OAuthTokens` if possible."""

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict) and "claudeAiOauth" in raw:
        raw = raw["claudeAiOauth"]
    try:
        parsed = _RawOAuthPayload.model_validate(raw)
    except ValidationError:
        return None
    return OAuthTokens(
        access_token=parsed.access_token,
        refresh_token=parsed.refresh_token,
        expires_at_ms=parsed.expires_at_ms,
        scopes=parsed.scopes,
        subscription_type=parsed.subscription_type,
    )
