"""Allowlist + risk policy. Enforced at three layers:
  1. before every agent/replay action (action type, target risk class)
  2. before every navigation the engine initiates
  3. in the browser itself: every network request is routed through `check_url`,
     so a click that would navigate somewhere unexpected is aborted too.
Risky (irreversible) actions are never executed on autopilot: discovery blocks them,
replay requires explicit caller confirmation on an *approved* capability, otherwise a
human approval intervention.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..util import norm_label

Risk = Literal["safe", "reversible", "irreversible"]


class PolicyViolation(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_origins: list[str]
    allowed_paths: list[str] = Field(default_factory=lambda: ["/**"])
    denied_paths: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=lambda: ["click", "fill", "select", "press", "extract"])
    irreversible_patterns: list[str] = Field(default_factory=list)
    risk_overrides: dict[str, Risk] = Field(default_factory=dict)
    static_asset_paths: list[str] = Field(default_factory=lambda: ["/favicon.ico"])

    @classmethod
    def load(cls, path, *, extra_origins: list[str] | None = None, overrides: dict | None = None,
             risk_overrides: dict[str, Risk] | None = None) -> "Policy":
        with open(path) as f:
            data = yaml.safe_load(f)
        data.update(overrides or {})
        p = cls.model_validate(data)
        if extra_origins:
            p.allowed_origins = sorted(set(p.allowed_origins) | set(extra_origins))
        if risk_overrides:
            p.risk_overrides = {**risk_overrides, **p.risk_overrides}
        return p

    # -- URLs ---------------------------------------------------------------------------
    def check_url(self, url: str) -> Decision:
        if url.startswith(("about:", "data:", "blob:")):
            return Decision(True)
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.allowed_origins:
            return Decision(False, f"origin {origin} is not allowlisted")
        path = parts.path or "/"
        if any(fnmatch.fnmatch(path, g) for g in self.denied_paths):
            return Decision(False, f"path {path} is explicitly denied")
        if path in self.static_asset_paths:
            return Decision(True)
        if not any(fnmatch.fnmatch(path, g) or (g.endswith("/**") and path == g[:-3]) for g in self.allowed_paths):
            return Decision(False, f"path {path} is not allowlisted")
        return Decision(True)

    # -- actions -------------------------------------------------------------------------
    def check_action(self, action: str) -> Decision:
        if action not in self.allowed_actions:
            return Decision(False, f"action type '{action}' is not allowed by policy")
        return Decision(True)

    def classify(self, action: str, role: str | None, name: str | None) -> Risk:
        """Risk of performing `action` on a control with this role/name.

        fill/select only change local form state -> reversible. Clicking a link or a
        navigation button is safe. A button whose label matches an irreversible pattern
        (confirm, post, transfer, delete ...) commits something -> irreversible.
        Explicit overrides (per app profile / tenant) win over patterns.
        """
        key = norm_label(name)
        for label, risk in self.risk_overrides.items():
            if norm_label(label) == key:
                return risk
        if action in ("fill", "select", "press"):
            return "reversible"
        if action == "extract":
            return "safe"
        if role in ("button", "menuitem") and any(re.search(p, name or "", re.I) for p in self.irreversible_patterns):
            return "irreversible"
        return "safe"
