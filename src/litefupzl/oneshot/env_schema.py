"""Environment schema for litefupzl oneshot mode."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from litefupzl.config.schema import FingerprintConfig
from litefupzl.config.defaults import FINGERPRINT_LOCALE, FINGERPRINT_TIMEZONE, FINGERPRINT_VIEWPORT


class OneShotEnvConfig(BaseModel):
    """Validated oneshot configuration loaded from environment variables."""

    site: Literal["linux.do"] = "linux.do"
    cookies: list[str] = Field(..., min_length=1)
    duration_minutes: int = Field(default=40, ge=1, le=240)
    headless: bool = True
    output_dir: str = "output/litefupzl"
    browser_name: Literal["camoufox", "chromium", "firefox", "patchright-chromium"] = "camoufox"
    proxy_server: str | None = None
    virtual_display: bool = True
    cookie_refresh_enabled: bool = True
    actions_admin_token: str | None = Field(default=None, exclude=True, repr=False)
    mutual_like_users: list[str] = Field(default_factory=list)
    monthly_topic_target: int = Field(default=500, ge=0, le=100_000)
    schedule_runs_per_day: int = Field(default=2, ge=1, le=24)
    topic_prefetch_pages: int = Field(default=7, ge=1, le=10)
    topic_prefetch_max_pages: int = Field(default=10, ge=1, le=10)
    fingerprint: FingerprintConfig = Field(
        default_factory=lambda: FingerprintConfig(
            viewport=list(FINGERPRINT_VIEWPORT),
            timezone=FINGERPRINT_TIMEZONE,
            locale=FINGERPRINT_LOCALE,
        )
    )

    @property
    def new_topic_target_per_run(self) -> int:
        """Return the minimum never-read topics expected from each scheduled run."""
        if self.monthly_topic_target <= 0:
            return 0
        return math.ceil(self.monthly_topic_target / (30 * self.schedule_runs_per_day))

    @field_validator("cookies")
    @classmethod
    def validate_non_empty_items(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value and value.strip()]
        if not cleaned:
            raise ValueError("list must contain at least one non-empty item")
        return cleaned

    @field_validator("mutual_like_users")
    @classmethod
    def validate_mutual_like_users(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            username = str(value).strip()
            if not username:
                continue
            key = username.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(username)
        return cleaned

    @model_validator(mode="after")
    def validate_topic_prefetch_range(self) -> "OneShotEnvConfig":
        if self.topic_prefetch_max_pages < self.topic_prefetch_pages:
            raise ValueError("topic_prefetch_max_pages must be >= topic_prefetch_pages")
        return self


class CFProbeEnvConfig(BaseModel):
    """Browser-only settings for the unauthenticated Cloudflare probe."""

    headless: bool = True
    output_dir: str = "output/litefupzl"
    browser_name: Literal["camoufox", "chromium", "firefox", "patchright-chromium"] = "camoufox"
    proxy_server: str | None = None
    virtual_display: bool = True
    fingerprint: FingerprintConfig = Field(
        default_factory=lambda: FingerprintConfig(
            viewport=list(FINGERPRINT_VIEWPORT),
            timezone=FINGERPRINT_TIMEZONE,
            locale=FINGERPRINT_LOCALE,
        )
    )
