"""Turns a successful discovery trace into a capability artifact.

What gets decided here (all deterministic, no model involved):
  * targets: role + visible name, plus table context only when the name alone is ambiguous
    (or the row is keyed by an input, e.g. the 'View' link in the row for {{member_id}});
    `nth` only as a last resort; xpath kept as a drift-flagged fallback
  * canonicalisation: concrete input values -> {{param}} tokens everywhere
  * checkpoints: derived from what changed on screen after each action (frame URL path +
    new stable headings); never from the model's own claims
  * risk class per step from the policy; side_effects = worst step
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ..safety.policy import Policy
from ..schema.artifact import (AppRef, Capability, Expectation, Fallback, OutcomeSpec, OutputSpec, ParamSpec,
                               Provenance, Step, SuccessCondition, Target, TargetContext)
from ..schema.profile import AppProfile, TenantConfig
from ..surface.base import Observation, UIItem, resolve_in
from ..util import norm_label, utcnow

RISK_ORDER = ["safe", "reversible", "irreversible"]


@dataclass
class TraceEntry:
    action: str
    item: UIItem
    value: str | None
    why: str
    before: Observation
    after: Observation
    origin: str = "model"


@dataclass
class ExtractEntry:
    name: str
    type: str
    description: str
    item: UIItem
    obs: Observation


@dataclass
class GoalSpec:
    capability: str
    title: str
    goal: str
    inputs: list[ParamSpec]
    values: dict[str, str] = field(default_factory=dict)  # discovery-time values, never persisted


class Recorder:
    def __init__(self, policy: Policy, profile: AppProfile, tenant: TenantConfig, spec: GoalSpec):
        self.policy, self.profile, self.tenant, self.spec = policy, profile, tenant, spec

    # --- canonicalisation ---------------------------------------------------------------------------
    def canon(self, s: str | None) -> str | None:
        if not s:
            return s
        for name, value in sorted(self.spec.values.items(), key=lambda kv: -len(kv[1] or "")):
            if value:
                s = re.sub(rf"(?<![\w]){re.escape(value)}(?![\w])", "{{" + name + "}}", s)
        return s

    # --- targets ----------------------------------------------------------------------------------------
    def target_for(self, item: UIItem, obs: Observation) -> Target:
        name = self.canon(item.name) if item.kind == "control" else None
        role = item.role if item.kind == "control" else "text"
        base = Target(kind=item.kind, role=role, name=name, context=TargetContext(frame=item.frame),
                      fallbacks=[Fallback(strategy="xpath", value=item.locator, note="recorded DOM path; drift if used")]
                      if item.locator else [])
        row = self.canon(item.ctx.get("row_label"))
        col = item.ctx.get("column_header")
        keyed_by_input = bool(row and "{{" in row)
        values = self.spec.values
        candidates: list[Target] = []
        if item.kind == "control":
            candidates.append(base)
            if row:
                candidates.append(base.model_copy(update={"context": TargetContext(frame=item.frame, row_label=row)}))
            if row and col:
                candidates.append(base.model_copy(update={"context": TargetContext(frame=item.frame, row_label=row,
                                                                                  column_header=col)}))
            if keyed_by_input:  # the row identity *is* the input: always pin it
                candidates = candidates[1:] or candidates
        else:  # data is identified by where it sits, never by its (variable) value
            ctx = TargetContext(frame=item.frame, row_label=row, column_header=col)
            candidates.append(base.model_copy(update={"context": ctx}))
        for t in candidates:
            res = resolve_in(obs.items, t, values, {})
            if not isinstance(res, str) and res.item.ref == item.ref:
                return t
        # still ambiguous: pin the index among semantic matches of the most specific candidate
        t = candidates[-1]
        pool = [i for i in obs.items if (t.context.frame in (None, i.frame))
                and not isinstance(resolve_in([i], t, values, {}), str)]
        nth = next((k for k, i in enumerate(pool) if i.ref == item.ref), None)
        return t.model_copy(update={"nth": nth})

    # --- checkpoints -------------------------------------------------------------------------------------
    def expectation_for(self, before: Observation, after: Observation) -> Expectation | None:
        changed = [f for f in after.frames
                   if urlsplit(f.url).path != urlsplit(before.frame_url(f.name) or "").path and f.name != "top"]
        frame = changed[0].name if changed else None
        before_texts = {norm_label(t) for t in before.texts(frame)}
        fresh = [i for i in after.items if i.kind == "text" and (frame is None or i.frame == frame)
                 and norm_label(i.name) not in before_texts]
        stable = [i.name for i in sorted(fresh, key=lambda i: not i.emphasis)
                  if 3 <= len(i.name) <= 60 and not re.search(r"\d|\[|\{\{", self.canon(i.name) or "")
                  and not i.ctx]  # table cells are data, not page identity
        if not changed and not stable:
            return None
        url_pattern = None
        if changed:
            url_pattern = urlsplit(changed[0].url).path + "*"
        return Expectation(frame=frame, url_pattern=url_pattern, text_present=stable[:1])

    # --- assembly -----------------------------------------------------------------------------------------
    def build(self, *, trace: list[TraceEntry], extracts: list[ExtractEntry], success_item: UIItem,
              final_obs: Observation, run_id: str, model: str, redacted_goal: str) -> Capability:
        steps: list[Step] = []
        for n, t in enumerate(trace, 1):
            target = self.target_for(t.item, t.before)
            risk = self.policy.classify(t.action, t.item.role, t.item.name)
            intent = t.why.strip()[:140] if t.why else f"{t.action} {t.item.role} '{t.item.name}'"
            steps.append(Step(id=f"s{n}", intent=self.canon(intent), action=t.action, target=target,
                              value=self.canon(t.value), risk=risk, origin=t.origin,
                              expect=self.expectation_for(t.before, t.after)))
        outputs = []
        for e in extracts:
            sens = "financial" if e.type == "money" else "internal"
            if e.item.ctx and any(norm_label(v) in {norm_label(s) for s in self.profile.sensitive_labels}
                                  for v in e.item.ctx.values()):
                sens = "pii"
            outputs.append(OutputSpec(name=e.name, type=e.type, description=e.description,
                                      source=self.target_for(e.item, e.obs), sensitivity=sens))
        success = Expectation(frame=success_item.frame, text_present=[self.canon(success_item.name)])
        side = max((s.risk for s in steps), key=RISK_ORDER.index, default="safe")
        outcomes = [OutcomeSpec(code=d.outcome_code, detector=d.id, description=d.message)
                    for d in self.profile.detectors if d.classification == "business" and d.outcome_code]
        return Capability(
            id=f"{self.profile.app_id}.{self.spec.capability}", name=self.spec.capability, version=1,
            title=self.spec.title, description=self.canon(self.spec.goal), side_effects=side,
            app=AppRef(app_id=self.profile.app_id, surface=self.profile.surface,
                       compatible_versions=self.profile.version_range, recorded_on_tenant=self.tenant.tenant_id,
                       recorded_on_version=self.tenant.product_version),
            inputs=self.spec.inputs, outputs=outputs, outcomes=outcomes, steps=steps,
            success=SuccessCondition(all_of=[success], require_outputs=bool(outputs)),
            provenance=Provenance(source_run_id=run_id, recorded_at=utcnow(), model=model,
                                  discovery_goal=redacted_goal,
                                  human_steps=sum(1 for t in trace if t.origin == "human")))
