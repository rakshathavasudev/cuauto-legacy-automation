"""The replay result contract returned to the calling agent.

Four terminal statuses, deliberately distinct:
  success           goal reached, checkpoint verified, outputs returned
  business_outcome  a legitimate answer the caller must handle (e.g. RECORD_NOT_FOUND)
  failed            hard failure: stopped, with step / expected / observed / evidence
  rejected          refused before touching the UI (bad args, unapproved, incompatible)
Recoverable conditions are not a status: they are handled and reported in `recoveries`.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    FAILED = "failed"
    REJECTED = "rejected"


class Outcome(BaseModel):
    code: str
    message: str
    detector: str | None = None
    step_id: str | None = None


class Failure(BaseModel):
    code: str
    message: str
    step_id: str | None = None
    step_intent: str | None = None
    expected: Any = None
    observed: Any = None
    retryable: bool = False
    evidence: list[str] = Field(default_factory=list)


class RecoveryRecord(BaseModel):
    step_id: str | None
    condition: str
    action: str
    attempt: int


class InterventionRecord(BaseModel):
    id: str
    kind: str
    reason: str
    resolution: str | None
    operator: str | None
    human_actions: int = 0


class LocatorRecord(BaseModel):
    step_id: str
    strategy: str
    drift: bool


class RunResult(BaseModel):
    run_id: str
    kind: str = "replay"
    capability_id: str | None = None
    capability_version: int | None = None
    tenant_id: str | None = None
    status: RunStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    outcome: Outcome | None = None
    failure: Failure | None = None
    recoveries: list[RecoveryRecord] = Field(default_factory=list)
    interventions: list[InterventionRecord] = Field(default_factory=list)
    locators: list[LocatorRecord] = Field(default_factory=list)
    completed_with_human: bool = False
    started_at: str
    finished_at: str | None = None
    duration_ms: int | None = None
    evidence_dir: str | None = None
