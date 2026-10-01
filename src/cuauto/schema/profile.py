"""App profiles (per vendor product) and tenant configs (per institution).

A capability is recorded against an *app profile*, not a tenant. The profile owns what
is common to every tenant running that vendor product: how to sign on, which runtime
states exist (detectors) and how to classify/recover them, which labels are sensitive.
Tenants add only their deltas: base URL, credentials reference, product version,
label aliases and policy overrides.
"""
from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .artifact import Expectation, Step, Target


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DetectorMatch(_M):
    text_regex: str
    frame: str | None = None


class RecoveryAction(_M):
    kind: Literal["click", "reauthenticate_and_restart", "wait_retry"]
    target: Target | None = None
    max_attempts: int = 2


class Detector(_M):
    id: str
    classification: Literal["business", "recoverable", "hard"]
    match: DetectorMatch
    outcome_code: str | None = None
    message: str
    recovery: RecoveryAction | None = None


class AuthSpec(_M):
    entry_path: str = "/login"
    steps: list[Step]
    success: Expectation


class AppProfile(_M):
    app_id: str
    product: str
    surface: Literal["web", "legacy_web", "desktop"]
    version_range: str
    auth: AuthSpec
    home_path: str = "/"
    detectors: list[Detector]
    sensitive_labels: list[str] = Field(default_factory=list)
    risk_overrides: dict[str, Literal["safe", "reversible", "irreversible"]] = Field(default_factory=dict)
    label_aliases: dict[str, list[str]] = Field(default_factory=dict)

    @classmethod
    def load(cls, path) -> "AppProfile":
        with open(path) as f:
            return cls.model_validate(yaml.safe_load(f))


class Credentials(_M):
    username_env: str
    password_env: str


class TenantConfig(_M):
    tenant_id: str
    display_name: str
    app_id: str
    product_version: str
    base_url: str
    credentials: Credentials
    label_aliases: dict[str, list[str]] = Field(default_factory=dict)
    policy: dict = Field(default_factory=dict)

    @classmethod
    def load(cls, path) -> "TenantConfig":
        with open(path) as f:
            return cls.model_validate(yaml.safe_load(f))
