"""Deterministic replay of a capability: the production path an AI agent invokes.

No model is consulted. Order of operations:
  1. contract checks without touching the UI -> `rejected`
     (args vs. input schema, approval state, tenant product version vs. compatible range)
  2. authenticate (app profile), then run steps: resolve -> gate risk -> act -> checkpoint
  3. runtime states along the way are classified by app-profile detectors:
       business    -> stop, status=business_outcome (a legitimate answer)
       recoverable -> handle (dismiss interstitial / re-auth + restart), record, continue
       hard        -> stop, status=failed with evidence
  4. unexplained states (no detector, checkpoint never reached): retry once if the step is
     safe, then escalate to a human (if allowed) or fail with step/expected/observed
  5. extract typed outputs, verify the success condition, return the result
"""
from __future__ import annotations

import json
import time
from decimal import Decimal, InvalidOperation

from ..evidence import RunLog
from ..handoff.controller import ControlLost, HandoffController
from ..runtime import Environment
from ..safety.policy import PolicyViolation
from ..schema.artifact import Capability, OutputSpec
from ..schema.artifact import Step
from ..schema.result import Failure, LocatorRecord, Outcome, RecoveryRecord, RunResult, RunStatus
from ..surface.base import Resolution
from ..util import utcnow, version_in_range
from .executor import (BusinessOutcomeSignal, ExecState, Executor, HardFailure, RestartSignal, Stuck,
                       summarize)

REDACT_OUTPUTS = {"pii", "financial", "secret"}


def parse_output(spec: OutputSpec, raw: str | None):
    if raw is None:
        raise ValueError("no text")
    s = raw.strip()
    if spec.type in ("money", "decimal"):
        cleaned = s.replace("$", "").replace(",", "").replace(" ", "")
        neg = cleaned.startswith("(") and cleaned.endswith(")")
        cleaned = cleaned.strip("()")
        try:
            d = Decimal(cleaned)
        except InvalidOperation:
            raise ValueError(f"not a {spec.type}") from None
        return str(-d if neg else d)
    if spec.type == "integer":
        return int(s.replace(",", ""))
    if spec.type == "boolean":
        return s.lower() in ("y", "yes", "true", "1", "active")
    return s


class ReplayEngine:
    def __init__(self, env: Environment, cap: Capability, registry_row: dict, *, on_stuck: str = "fail",
                 confirm_irreversible: bool = False, allow_draft: bool = False, headed: bool = False,
                 escalation_timeout_s: int = 600, echo: bool = True):
        self.env, self.cap, self.row = env, cap, registry_row
        self.on_stuck = on_stuck
        self.confirm_irreversible = confirm_irreversible
        self.allow_draft = allow_draft
        self.headed = headed
        self.escalation_timeout_s = escalation_timeout_s
        self.echo = echo

    # --------------------------------------------------------------------------------------------------
    def run(self, args: dict[str, str]) -> RunResult:
        run_id, edir = self.env.new_evidence_dir("replay", self.cap.name)
        started, t0 = utcnow(), time.monotonic()
        redactor = self.env.redactor()
        for p in self.cap.inputs:
            if p.sensitivity != "public":
                redactor.register_value(args.get(p.name), p.name)
        repo = self.env.repo
        repo.create_run(kind="replay", tenant_id=self.env.tenant.tenant_id, evidence_dir=str(edir), run_id=run_id,
                        capability_id=self.cap.id, capability_version=self.cap.version)
        runlog = RunLog(run_id, edir, repo, redactor, echo=self.echo)
        result = RunResult(run_id=run_id, capability_id=self.cap.id, capability_version=self.cap.version,
                           tenant_id=self.env.tenant.tenant_id, status=RunStatus.SUCCESS, started_at=started,
                           evidence_dir=runlog.rel(edir))
        runlog.event("replay_start", None, capability=self.cap.id, version=self.cap.version,
                     registry_status=self.row["status"], args=args, on_stuck=self.on_stuck)

        rejection = self._precheck(args)
        if rejection:
            result.status = RunStatus.REJECTED
            result.failure = Failure(code=rejection[0], message=rejection[1], retryable=False)
            return self._finish(result, runlog, t0, None)

        surface = self.env.new_surface(headed=self.headed)
        surface.start()
        state = ExecState()
        handoff = HandoffController(repo, run_id, surface, runlog, timeout_s=self.escalation_timeout_s,
                                    console_url=self.env.settings.operator_console_url)
        ex = Executor(self.env, surface, runlog, handoff, state=state)
        try:
            ex.authenticate()
            self._run_steps(ex, handoff, args)
            result.outputs = self._extract(ex, args)
            self._verify_success(ex, args)
            runlog.event("success_verified", None, outputs=self._persistable(result.outputs))
        except BusinessOutcomeSignal as b:
            declared = any(o.code == b.detector.outcome_code for o in self.cap.outcomes)
            result.status = RunStatus.BUSINESS_OUTCOME
            result.outcome = Outcome(code=b.detector.outcome_code, message=b.detector.message,
                                     detector=b.detector.id, step_id=b.step_id)
            runlog.event("business_outcome", b.step_id, code=b.detector.outcome_code, declared=declared)
        except (HardFailure, PolicyViolation, ControlLost) as e:
            result.status = RunStatus.FAILED
            result.failure = self._failure(e, surface, runlog)
        except Exception as e:  # engine bug or surface crash: still a structured, debuggable failure
            result.status = RunStatus.FAILED
            result.failure = self._failure(HardFailure("ENGINE_ERROR", f"{type(e).__name__}: {e}"), surface, runlog)
        finally:
            result.recoveries, result.locators = state.recoveries, state.locators
            result.interventions = handoff.records
            result.completed_with_human = any(r.human_actions for r in handoff.records)
            try:
                if result.status == RunStatus.SUCCESS:
                    surface.screenshot(str(runlog.path("screens", "final.png")))
            finally:
                surface.close()
        return self._finish(result, runlog, t0, state)

    # --------------------------------------------------------------------------------------------------
    def _precheck(self, args: dict[str, str]) -> tuple[str, str] | None:
        errs = self.cap.validate_args(args)
        if errs:
            return "INVALID_ARGUMENTS", "; ".join(errs)
        if self.row["status"] != "approved" and not self.allow_draft:
            return "NOT_APPROVED", (f"{self.cap.id} v{self.cap.version} is '{self.row['status']}'; "
                                    "approve it or pass --allow-draft for supervised runs")
        if self.cap.app.app_id != self.env.tenant.app_id:
            return "WRONG_APP", f"capability targets {self.cap.app.app_id}, tenant runs {self.env.tenant.app_id}"
        if not version_in_range(self.env.tenant.product_version, self.cap.app.compatible_versions):
            return "INCOMPATIBLE_VERSION", (f"tenant runs {self.env.tenant.product_version}, capability supports "
                                            f"{self.cap.app.compatible_versions}; re-record or add an override")
        return None

    def _run_steps(self, ex: Executor, handoff: HandoffController, args: dict[str, str]) -> None:
        steps = self.cap.steps
        i, restarts, retried = 0, 0, set()
        while i < len(steps):
            step = steps[i]
            try:
                ex.run_step(step, args, gate=lambda s, r: self._gate(ex, handoff, s, r))
                i += 1
            except RestartSignal as r:
                if ex.state.committed:
                    raise HardFailure("SESSION_LOST_AFTER_COMMIT",
                                      "session expired after an irreversible step; outcome unknown, needs a human",
                                      step=step) from None
                restarts += 1
                ex.runlog.event("flow_restart", step.id, reason=r.detector.id, restart=restarts)
                ex.authenticate()
                i = 0
            except Stuck as st:
                if step.risk == "safe" and step.id not in retried and st.kind != "target_ambiguous":
                    retried.add(step.id)
                    ex.runlog.event("recovery", step.id, detector="transient", action="retry_step", attempt=1,
                                    reason=st.kind)
                    ex.state.recoveries.append(RecoveryRecord(step_id=step.id, condition=f"transient:{st.kind}",
                                                              action="retry_step", attempt=1))
                    continue
                if self.on_stuck != "escalate":
                    raise HardFailure(st.kind.upper(), f"step '{step.intent}' could not proceed", step=step,
                                      expected=st.expected, observed=st.observed) from None
                resolution, actions = handoff.escalate(
                    kind="stuck", step_id=step.id,
                    reason=f"{st.kind} at step {step.id} ({step.intent}); screen shows: "
                           f"{' | '.join((st.observed or {}).get('headline_text', [])[:3]) or 'unknown'}",
                    context={"capability": self.cap.id, "version": self.cap.version, "step": step.id,
                             "intent": step.intent, "expected": st.expected, "observed": st.observed},
                    obs=st.obs)
                if resolution != "resume":
                    raise HardFailure("ABORTED_BY_OPERATOR" if resolution == "abort" else "ESCALATION_TIMEOUT",
                                      f"escalation ended with '{resolution}'", step=step) from None
                self._account_human(ex, actions)
                i = self._resync(ex, i, args)

    def _gate(self, ex: Executor, handoff: HandoffController, step, res: Resolution) -> None:
        if self.confirm_irreversible and self.row["status"] == "approved":
            ex.runlog.event("irreversible_confirmed", step.id, by="caller", control=res.item.name)
            return
        if self.on_stuck != "escalate":
            raise HardFailure("APPROVAL_REQUIRED",
                              f"'{res.item.name}' is irreversible; needs --confirm-irreversible on an approved "
                              "capability, or --on-stuck escalate for a human decision", step=step, retryable=True)
        resolution, _ = handoff.escalate(kind="approval", reason=f"approve irreversible action '{res.item.name}' "
                                                                  f"({step.intent})", step_id=step.id,
                                         context={"capability": self.cap.id, "step": step.id,
                                                  "control": res.item.name})
        if resolution != "approve":
            raise HardFailure("APPROVAL_DENIED", f"operator resolution: {resolution}", step=step)

    def _account_human(self, ex: Executor, actions: list[dict]) -> None:
        for a in actions:
            t = json.loads(a["target_json"])
            if ex.env.policy.classify(a["action"], t.get("role"), t.get("name")) == "irreversible":
                ex.state.committed = True
                ex.runlog.event("human_committed", None, control=t.get("name"))

    def _resync(self, ex: Executor, i: int, args: dict[str, str]) -> int:
        """After a human hands back, find where the flow now stands instead of assuming."""
        obs = ex.surface.observe()
        if all(ex.expectation_met(obs, e, args)[0] for e in self.cap.success.all_of):
            ex.runlog.event("resync", None, resume_at="success_check")
            return len(self.cap.steps)
        for j in range(len(self.cap.steps) - 1, i - 1, -1):
            exp = self.cap.steps[j].expect
            if exp and ex.expectation_met(obs, exp, args)[0]:
                ex.runlog.event("resync", self.cap.steps[j].id, resume_at=j + 1)
                return j + 1
        if isinstance(ex.surface.resolve(self.cap.steps[i].target, args, ex.env.aliases, obs), Resolution):
            ex.runlog.event("resync", self.cap.steps[i].id, resume_at=i)
            return i
        raise HardFailure("RESYNC_FAILED", "state after handoff matches no known checkpoint",
                          step=self.cap.steps[i], observed=summarize(obs, ex.redactor))

    def _extract(self, ex: Executor, args: dict[str, str]) -> dict:
        outputs = {}
        for spec in self.cap.outputs:
            probe = Step(id=f"out:{spec.name}", intent=f"read {spec.name}", action="click", target=spec.source)
            try:
                res, _ = ex.wait_target(spec.source, args, probe, timeout_ms=5000)
            except Stuck as st:
                if spec.required:
                    raise HardFailure("OUTPUT_NOT_FOUND", f"could not locate output '{spec.name}'",
                                      expected=st.expected, observed=st.observed) from None
                outputs[spec.name] = None
                continue
            raw = ex.surface.read(res.item)
            try:
                outputs[spec.name] = parse_output(spec, raw)
            except ValueError as e:
                raise HardFailure("OUTPUT_TYPE_MISMATCH", f"output '{spec.name}' is not a {spec.type}: {e}",
                                  expected=spec.type, observed=ex.redactor.text(raw)) from None
            ex.state.locators.append(LocatorRecord(step_id=probe.id, strategy=res.strategy, drift=res.drift))
        return outputs

    def _verify_success(self, ex: Executor, args: dict[str, str]) -> None:
        for exp in self.cap.success.all_of:
            try:
                ex.wait_expectation(exp, args, None)
            except Stuck as st:
                raise HardFailure("SUCCESS_CONDITION_NOT_MET", "final checkpoint not satisfied",
                                  expected=st.expected, observed=st.observed) from None

    # --------------------------------------------------------------------------------------------------
    def _failure(self, e: Exception, surface, runlog: RunLog) -> Failure:
        evidence = []
        try:
            evidence.append(runlog.rel(surface.screenshot(str(runlog.path("screens", f"{runlog.seq + 1:03d}_failure.png")))))
            evidence.append(runlog.snapshot(surface.observe(), "failure"))
        except Exception:
            pass
        if surface.blocked_requests:
            evidence.append(f"blocked_requests: {runlog.redactor.text('; '.join(surface.blocked_requests))}")
        if isinstance(e, HardFailure):
            f = Failure(code=e.code, message=e.message, step_id=e.step.id if e.step else None,
                        step_intent=e.step.intent if e.step else None, expected=e.expected, observed=e.observed,
                        retryable=e.retryable, evidence=evidence)
        elif isinstance(e, PolicyViolation):
            f = Failure(code="POLICY_VIOLATION", message=e.reason, evidence=evidence)
        else:
            f = Failure(code="CONTROL_LOST", message=str(e), evidence=evidence)
        runlog.event("failure", f.step_id, code=f.code, message=f.message, expected=f.expected, observed=f.observed)
        return f

    def _persistable(self, outputs: dict) -> dict:
        """Outputs as they may be written to disk/DB: sensitive ones by declared sensitivity."""
        sens = {o.name: o.sensitivity for o in self.cap.outputs}
        return {k: (f"[REDACTED:{sens.get(k)}]" if sens.get(k) in REDACT_OUTPUTS else v) for k, v in outputs.items()}

    def _finish(self, result: RunResult, runlog: RunLog, t0: float, state) -> RunResult:
        result.finished_at = utcnow()
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        persisted = result.model_dump(mode="json")
        persisted["outputs"] = self._persistable(result.outputs)
        runlog.write_json("result.json", persisted)
        code = (result.outcome and result.outcome.code) or (result.failure and result.failure.code)
        self.env.repo.finish_run(result.run_id, result.status.value, code)
        runlog.event("replay_end", None, status=result.status.value, code=code, duration_ms=result.duration_ms)
        runlog.close()
        return result
