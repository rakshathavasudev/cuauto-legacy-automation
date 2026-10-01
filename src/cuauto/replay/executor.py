"""Deterministic execution primitives shared by replay and authentication.

The core loop is "observe -> classify runtime state -> act -> wait for checkpoint".
Waiting is always *for a state* (a checkpoint or a detector), never for a fixed time,
which is what absorbs transient slowness without flakiness.
"""
from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..evidence import RunLog
from ..handoff.controller import HandoffController
from ..runtime import Environment
from ..schema.artifact import Expectation, Step, Target
from ..schema.profile import Detector
from ..schema.result import LocatorRecord, RecoveryRecord
from ..surface.base import Observation, Resolution, names_for
from ..util import norm_label, render

RISK_ORDER = ["safe", "reversible", "irreversible"]
POLL_MS = 300


# --- control-flow signals ------------------------------------------------------------------------
class BusinessOutcomeSignal(Exception):
    def __init__(self, detector: Detector, step_id: str | None):
        super().__init__(detector.outcome_code)
        self.detector, self.step_id = detector, step_id


class HardFailure(Exception):
    def __init__(self, code: str, message: str, *, step: Step | None = None, expected: Any = None,
                 observed: Any = None, retryable: bool = False):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.step = code, message, step
        self.expected, self.observed, self.retryable = expected, observed, retryable


class RestartSignal(Exception):
    def __init__(self, detector: Detector, step_id: str | None):
        super().__init__(detector.id)
        self.detector, self.step_id = detector, step_id


class Stuck(Exception):
    """No detector explains the state and the expected target/checkpoint never appeared."""

    def __init__(self, kind: str, step: Step, expected: Any, observed: Any, obs: Observation | None):
        super().__init__(kind)
        self.kind, self.step, self.expected, self.observed, self.obs = kind, step, expected, observed, obs


@dataclass
class ExecState:
    recoveries: list[RecoveryRecord] = field(default_factory=list)
    locators: list[LocatorRecord] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    committed: bool = False  # an irreversible action has been performed in this run


def summarize(obs: Observation, redactor, limit: int = 12) -> dict:
    """Short, redacted description of what is on screen (used in failures/escalations)."""
    return {
        "frames": {f.name: redactor.text(urlsplit(f.url).path) for f in obs.frames},
        "headline_text": [redactor.redact_item(i.name, i.ctx) for i in obs.items if i.kind == "text"][:limit],
        "controls": [f"{i.role} '{redactor.text(i.name)}'" for i in obs.items if i.kind == "control"][:limit],
    }


class Executor:
    def __init__(self, env: Environment, surface, runlog: RunLog, handoff: HandoffController, *,
                 state: ExecState | None = None):
        self.env, self.surface, self.runlog, self.handoff = env, surface, runlog, handoff
        self.redactor = runlog.redactor
        self.state = state or ExecState()

    # --- runtime state classification -----------------------------------------------------------
    def match_detector(self, obs: Observation) -> Detector | None:
        for d in self.env.profile.detectors:
            rx = re.compile(d.match.text_regex)
            for item in obs.items:
                if d.match.frame and item.frame != d.match.frame:
                    continue
                if item.kind == "text" and rx.search(item.name):
                    return d
        return None

    def handle_detector(self, d: Detector, obs: Observation, step: Step | None) -> None:
        sid = step.id if step else None
        self.runlog.event("state_detected", sid, detector=d.id, classification=d.classification, message=d.message)
        if d.classification == "business":
            raise BusinessOutcomeSignal(d, sid)
        if d.classification == "hard":
            raise HardFailure(d.id.upper(), d.message, step=step, observed=summarize(obs, self.redactor))
        rec = d.recovery
        n = self.state.attempts.get(d.id, 0) + 1
        self.state.attempts[d.id] = n
        if rec is None or n > rec.max_attempts:
            raise HardFailure("RECOVERY_EXHAUSTED", f"'{d.id}' persisted after {n - 1} recovery attempts",
                              step=step, observed=summarize(obs, self.redactor))
        self.state.recoveries.append(RecoveryRecord(step_id=sid, condition=d.id, action=rec.kind, attempt=n))
        self.runlog.event("recovery", sid, detector=d.id, action=rec.kind, attempt=n)
        if rec.kind == "reauthenticate_and_restart":
            raise RestartSignal(d, sid)
        if rec.kind == "click":
            res = self.surface.resolve(rec.target, {}, self.env.aliases, obs)
            if not isinstance(res, Resolution):
                raise HardFailure("RECOVERY_FAILED", f"recovery control for '{d.id}' not found: {res}", step=step)
            self.handoff.assert_in_control()
            self.surface.act("click", res.item)
            self._wait_cleared(d)
        # wait_retry: nothing to do, the caller keeps polling

    def _wait_cleared(self, d: Detector, timeout_ms: int = 5000) -> None:
        """After a recovery click, wait for the state to go away before re-classifying,
        otherwise the same (still-visible) interstitial would be 'recovered' twice."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            self.surface.pump(POLL_MS)
            try:
                if self.match_detector(self.surface.observe()) is not d:
                    return
            except Exception:  # frame navigating mid-observation
                continue

    # --- checkpoints ------------------------------------------------------------------------------
    def expectation_met(self, obs: Observation, exp: Expectation, values: dict[str, str]) -> tuple[bool, str]:
        if exp.url_pattern:
            urls = [f.url for f in obs.frames if exp.frame in (None, f.name)]
            paths = [urlsplit(u).path + ("?" + urlsplit(u).query if urlsplit(u).query else "") for u in urls]
            if not any(fnmatch.fnmatch(p, render(exp.url_pattern, values)) for p in paths):
                return False, f"no frame url matches {exp.url_pattern}"
        texts = [norm_label(i.name) for i in obs.items if i.kind == "text" and exp.frame in (None, i.frame)]
        for want in exp.text_present:
            options = names_for(want, values, self.env.aliases)
            if not any(any(o and o in t for o in options) for t in texts):
                return False, f"text '{want}' not present"
        for bad in exp.text_absent:
            if any(norm_label(render(bad, values)) in t for t in texts):
                return False, f"text '{bad}' present"
        return True, "ok"

    def wait_expectation(self, exp: Expectation, values: dict[str, str], step: Step | None) -> Observation:
        deadline = time.monotonic() + exp.timeout_ms / 1000
        last, obs = "", None
        while True:
            obs = self.surface.observe()
            d = self.match_detector(obs)
            if d:
                self.handle_detector(d, obs, step)
                continue
            ok, last = self.expectation_met(obs, exp, values)
            if ok:
                return obs
            if time.monotonic() > deadline:
                raise Stuck("checkpoint_not_reached", step, exp.model_dump(exclude_none=True),
                            {"reason": last, **summarize(obs, self.redactor)}, obs)
            self.surface.pump(POLL_MS)

    def wait_target(self, target: Target, values: dict[str, str], step: Step, timeout_ms: int = 8000
                    ) -> tuple[Resolution, Observation]:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            obs = self.surface.observe()
            d = self.match_detector(obs)
            if d:
                self.handle_detector(d, obs, step)
                continue
            res = self.surface.resolve(target, values, self.env.aliases, obs)
            if isinstance(res, Resolution):
                return res, obs
            if res.startswith("ambiguous") or time.monotonic() > deadline:
                raise Stuck("target_not_found" if not res.startswith("ambiguous") else "target_ambiguous", step,
                            target.model_dump(exclude_none=True, exclude_defaults=True),
                            {"reason": res, **summarize(obs, self.redactor)}, obs)
            self.surface.pump(POLL_MS)

    # --- one step -----------------------------------------------------------------------------------
    def run_step(self, step: Step, values: dict[str, str], *, gate=None) -> None:
        self.handoff.assert_in_control()
        d = self.env.policy.check_action(step.action)
        if not d.allowed:
            raise HardFailure("POLICY_VIOLATION", d.reason, step=step)
        timeout = step.expect.timeout_ms if step.expect else 8000
        res, _ = self.wait_target(step.target, values, step, timeout)
        runtime_risk = self.env.policy.classify(step.action, res.item.role, res.item.name)
        risk = max(step.risk, runtime_risk, key=RISK_ORDER.index)
        if risk == "irreversible":
            if gate is None:
                raise HardFailure("APPROVAL_REQUIRED", f"'{res.item.name}' is irreversible", step=step)
            gate(step, res)
        value = render(step.value, values) if step.value is not None else None
        self.handoff.assert_in_control()
        try:
            self.surface.act(step.action, res.item, value)
        except Exception as e:  # element vanished / not interactable: report, don't crash
            raise HardFailure("ACTION_FAILED", f"{step.action} on {res.item.role} '{res.item.name}' failed: "
                              f"{type(e).__name__}: {str(e).splitlines()[0][:160]}", step=step,
                              retryable=True) from None
        if risk == "irreversible":
            self.state.committed = True
        self.state.locators.append(LocatorRecord(step_id=step.id, strategy=res.strategy, drift=res.drift))
        self.runlog.event("step_action", step.id, intent=step.intent, action=step.action, risk=risk,
                          target=f"{res.item.role} '{res.item.name}'", frame=res.item.frame, strategy=res.strategy,
                          drift=res.drift, value=value if value is None or step.origin != "profile" else "[SECRET]")
        if step.expect:
            self.wait_expectation(step.expect, values, step)
            self.runlog.event("checkpoint_ok", step.id, expect=step.expect.model_dump(exclude_none=True))

    # --- authentication (app-profile owned; never seen by the model) ----------------------------------
    def authenticate(self) -> None:
        auth = self.env.profile.auth
        self.runlog.event("auth_start", None, tenant=self.env.tenant.tenant_id)
        self.surface.goto(self.env.url(auth.entry_path))
        values = dict(self.env.secrets)
        try:
            for step in auth.steps:
                self.run_step(step, values)
            self.wait_expectation(auth.success, values, None)
        except Stuck as st:
            raise HardFailure("AUTH_FAILED", f"sign-on did not complete ({st.kind})", step=st.step,
                              expected=st.expected, observed=st.observed) from None
        self.runlog.event("auth_ok", None)
