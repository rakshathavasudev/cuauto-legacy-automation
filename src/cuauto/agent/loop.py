"""Discovery: an LLM-driven observe -> decide -> act loop against the live surface.

The model never sees credentials (sign-on is deterministic, owned by the app profile),
never sees real input values (it types {{tokens}}), and never sees masked data (money,
PII). It picks *refs* from a semantic observation; the system does the acting, enforces
policy on every action, and records what actually happened for the Recorder.

Stopping conditions: done (verified against the screen), give_up, max steps, wall-clock
timeout, or a dead end (no visible change after several consecutive actions) which is
escalated to a human when allowed.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..evidence import RunLog
from ..handoff.controller import HandoffController
from ..runtime import Environment
from ..safety.redaction import Redactor
from ..schema.artifact import Capability, ParamSpec
from ..replay.executor import Executor, HardFailure, RestartSignal
from ..surface.base import Observation, UIItem
from ..util import norm_label, render, template_vars, utcnow
from .llm import LLMClient, image_block
from .prompts import SYSTEM, TOOLS
from .recorder import ExtractEntry, GoalSpec, Recorder, TraceEntry
from ..replay.engine import parse_output
from ..schema.artifact import OutputSpec, Target

KEEP_FULL_OBSERVATIONS = 3
DEAD_END_LIMIT = 3


def load_goal(path: Path) -> GoalSpec:
    data = yaml.safe_load(Path(path).read_text())
    inputs, values = [], {}
    for p in data["inputs"]:
        values[p["name"]] = str(p.pop("discovery_value"))
        inputs.append(ParamSpec.model_validate(p))
    return GoalSpec(capability=data["capability"], title=data["title"], goal=data["goal"], inputs=inputs,
                    values=values)


@dataclass
class DiscoveryResult:
    run_id: str
    status: str
    message: str
    capability: Capability | None
    evidence_dir: str
    steps_taken: int
    usage: dict


class DiscoveryAgent:
    def __init__(self, env: Environment, llm: LLMClient, spec: GoalSpec, *, max_steps: int = 25,
                 timeout_s: int = 300, vision: bool = True, headed: bool = False, allow_escalation: bool = True,
                 escalation_timeout_s: int = 600, echo: bool = True):
        self.env, self.llm, self.spec = env, llm, spec
        self.max_steps, self.timeout_s, self.vision = max_steps, timeout_s, vision
        self.headed, self.allow_escalation, self.escalation_timeout_s = headed, allow_escalation, escalation_timeout_s
        self.echo = echo

    # --- model-facing view ------------------------------------------------------------------------------
    def _model_redactor(self) -> Redactor:
        r = self.env.redactor()
        r.sensitive_labels = self.log_redactor.sensitive_labels
        for name, value in self.spec.values.items():
            r.register_value(value, name, replacement="{{" + name + "}}")
        return r

    def render_obs(self, obs: Observation, note: str = "") -> str:
        r = self.model_redactor
        lines = []
        if note:
            lines.append(f"NOTE: {note}")
        lines.append("Frames: " + ", ".join(f"{f.name}={r.text(f.url.split('://', 1)[-1].split('/', 1)[-1])}"
                                            for f in obs.frames))
        current = None
        for i in obs.items:
            if i.frame != current:
                current = i.frame
                lines.append(f"[frame {current}]")
            ctx = ", ".join(f"{k.split('_')[0] if k != 'column_header' else 'col'}: {r.text(v)}"
                            for k, v in i.ctx.items() if k in ("row_label", "column_header"))
            ctx = f" ({ctx})" if ctx else ""
            if i.kind == "text":
                lines.append(f"  {i.ref} text \"{r.redact_item(i.name, i.ctx)}\"{ctx}")
            else:
                extra = ""
                if i.role == "textbox":
                    extra = " value=[hidden]" if i.sensitive else f" value=\"{r.text(i.value or '')}\""
                if i.options:
                    extra = f" options={i.options}"
                if i.disabled:
                    extra += " disabled"
                lines.append(f"  {i.ref} {i.role} \"{r.text(i.name)}\"{extra}{ctx}")
        return "\n".join(lines)

    def _user_content(self, obs: Observation, note: str = "") -> list[dict]:
        content = [{"type": "text", "text": self.render_obs(obs, note)}]
        if self.vision and obs.screenshot_png:
            content.append(image_block(obs.screenshot_png))
        return content

    def _elide(self, messages: list[dict]) -> None:
        """Keep only the last few observations in full; drop older screenshots entirely."""
        seen = 0
        for m in reversed(messages):
            if m["role"] != "user":
                continue
            seen += 1
            blocks = m["content"]
            for b in blocks:
                target = b["content"] if b.get("type") == "tool_result" and isinstance(b.get("content"), list) else None
                if target is not None:
                    if seen > 1:
                        b["content"] = [c for c in target if c.get("type") != "image"]
                    if seen > KEEP_FULL_OBSERVATIONS:
                        b["content"] = [{"type": "text", "text": "(older observation elided)"}]
            if seen > 1:
                m["content"] = [b for b in blocks if b.get("type") != "image"]

    # --- the loop -------------------------------------------------------------------------------------------
    def run(self) -> DiscoveryResult:
        env = self.env
        run_id, edir = env.new_evidence_dir("discovery", self.spec.capability)
        self.log_redactor = env.redactor()
        for p in self.spec.inputs:
            if p.sensitivity != "public":
                self.log_redactor.register_value(self.spec.values.get(p.name), p.name)
        self.model_redactor = self._model_redactor()
        redacted_goal = self.spec.goal  # goal text uses {{tokens}}, never raw values
        env.repo.create_run(kind="discovery", tenant_id=env.tenant.tenant_id, evidence_dir=str(edir), run_id=run_id,
                            goal=redacted_goal)
        runlog = RunLog(run_id, edir, env.repo, self.log_redactor, echo=self.echo)
        transcript = open(edir / "transcript.jsonl", "a", encoding="utf-8")
        runlog.event("discovery_start", None, goal=redacted_goal, model=self.llm.name, tenant=env.tenant.tenant_id,
                      inputs=[p.name for p in self.spec.inputs], max_steps=self.max_steps)

        surface = env.new_surface(headed=self.headed)
        surface.start()
        handoff = HandoffController(env.repo, run_id, surface, runlog, timeout_s=self.escalation_timeout_s,
                                    console_url=env.settings.operator_console_url)
        ex = Executor(env, surface, runlog, handoff)
        trace: list[TraceEntry] = []
        extracts: list[ExtractEntry] = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        status, message, cap = "failed", "", None
        success_item: UIItem | None = None
        t0 = time.monotonic()
        steps = 0
        try:
            ex.authenticate()
            obs = self._settle(surface, None)
            token_list = ", ".join("{{" + p.name + "}} (" + p.description + ")" for p in self.spec.inputs)
            intro = (f"GOAL: {self.spec.goal}\nTask inputs available as tokens: {token_list}\n"
                     f"You are signed on. Current screen:\n")
            messages = [{"role": "user", "content": [{"type": "text", "text": intro}] + self._user_content(obs)}]
            no_change = 0
            while True:
                if steps >= self.max_steps:
                    message = f"stopped: reached max steps ({self.max_steps})"
                    break
                if time.monotonic() - t0 > self.timeout_s:
                    message = f"stopped: timeout ({self.timeout_s}s)"
                    break
                steps += 1
                self._elide(messages)
                d = self.llm.decide(SYSTEM, messages, TOOLS, obs)
                for k in usage:
                    usage[k] += d.usage.get(k, 0)
                red_input = self.model_redactor.obj(d.input)
                red_text = self.model_redactor.text(d.text) or ""
                runlog.event("model_decision", None, tool=d.tool, input=red_input, reasoning=red_text[:400],
                             usage=d.usage)
                transcript.write(json.dumps({"turn": steps, "ts": utcnow(), "reasoning": red_text, "tool": d.tool,
                                             "input": red_input}) + "\n")
                transcript.flush()
                messages.append({"role": "assistant", "content": d.assistant_content})

                result_text, new_obs, finished = self._apply(d, obs, ex, surface, handoff, runlog, trace, extracts)
                if finished == "done":
                    success_item = new_obs  # type: ignore[assignment]
                    status, message = "success", d.input.get("summary", "")
                    break
                if finished in ("give_up", "stop"):
                    message = result_text
                    break
                if new_obs.signature() == obs.signature() and d.tool == "click":  # edits legitimately change nothing
                    no_change += 1
                else:
                    no_change = 0
                note = ""
                if no_change >= DEAD_END_LIMIT:
                    runlog.event("dead_end", None, reason=f"{no_change} actions with no visible change")
                    if not self.allow_escalation:
                        message = "stopped: dead end"
                        break
                    note = self._escalate(ex, handoff, trace, new_obs, "dead end: several actions changed nothing")
                    new_obs = self._settle(surface, None)
                    no_change = 0
                obs = new_obs
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": d.tool_use_id,
                     "content": [{"type": "text", "text": result_text}] + self._user_content(obs, note)}]})
        except HardFailure as e:
            message = f"{e.code}: {e.message}"
            runlog.event("failure", None, code=e.code, message=e.message)
        except Exception as e:  # keep evidence on any crash
            message = f"ENGINE_ERROR: {type(e).__name__}: {e}"
            runlog.event("failure", None, code="ENGINE_ERROR", message=str(e))
        try:
            if status == "success" and isinstance(success_item, tuple):
                final_obs, item = success_item
                rec = Recorder(env.policy, env.profile, env.tenant, self.spec)
                cap = rec.build(trace=trace, extracts=extracts, success_item=item, final_obs=final_obs,
                                run_id=run_id, model=self.llm.name, redacted_goal=redacted_goal)
                cap = env.store.save_new_version(cap, run_id)
                (edir / "artifact.yaml").write_text(cap.to_yaml())
                runlog.event("artifact_saved", None, capability=cap.id, version=cap.version,
                             sha256=cap.content_hash(), steps=len(cap.steps), outputs=[o.name for o in cap.outputs])
            surface.screenshot(str(runlog.path("screens", "final.png")))
        except Exception as e:
            status, message = "failed", f"RECORDING_FAILED: {e}"
            runlog.event("failure", None, code="RECORDING_FAILED", message=str(e))
        finally:
            surface.close()
            transcript.close()
        env.repo.finish_run(run_id, "success" if status == "success" else "failed", None if cap else "NO_ARTIFACT")
        summary = {"run_id": run_id, "status": status, "message": message, "steps": steps, "usage": usage,
                   "model": self.llm.name, "capability": cap and {"id": cap.id, "version": cap.version}}
        runlog.write_json("result.json", summary)
        runlog.event("discovery_end", None, status=status, message=message, steps=steps, usage=usage)
        runlog.close()
        return DiscoveryResult(run_id=run_id, status=status, message=message, capability=cap,
                               evidence_dir=runlog.rel(edir), steps_taken=steps, usage=usage)

    # --- acting ---------------------------------------------------------------------------------------------
    def _settle(self, surface, before: Observation | None, max_ms: int = 10000) -> Observation:
        """Wait for the screen to stop changing (discovery has no checkpoint to wait on yet)."""
        deadline = time.monotonic() + max_ms / 1000
        surface.pump(400)
        prev = surface.observe()
        while time.monotonic() < deadline:
            surface.pump(400)
            cur = surface.observe()
            if cur.signature() == prev.signature() and (before is None or cur.signature() != before.signature()
                                                        or time.monotonic() > deadline - max_ms / 2000):
                break
            prev = cur
        return surface.observe(screenshot=self.vision)

    def _apply(self, d, obs: Observation, ex: Executor, surface, handoff, runlog, trace, extracts):
        tool, inp = d.tool, d.input
        policy = self.env.policy
        if tool in ("click", "fill", "select"):
            item = obs.by_ref(inp.get("ref", ""))
            if item is None or item.kind != "control":
                return f"ERROR: '{inp.get('ref')}' is not a control in the latest observation.", obs, None
            dec = policy.check_action(tool)
            if not dec.allowed:
                runlog.event("policy_block", None, tool=tool, reason=dec.reason)
                return f"BLOCKED by policy: {dec.reason}", obs, None
            risk = policy.classify(tool, item.role, item.name)
            if risk == "irreversible":
                runlog.event("policy_block", None, tool=tool, control=item.name, reason="irreversible action")
                return (f"BLOCKED by policy: '{item.name}' commits an irreversible change and is never executed "
                        "during discovery. If the goal requires it, call request_human; otherwise stop before it."
                        ), obs, None
            template = inp.get("text") if tool == "fill" else inp.get("option")
            unknown = [v for v in template_vars(template) if v not in self.spec.values]
            if unknown:
                return f"ERROR: unknown input token(s) {unknown}.", obs, None
            value = render(template, self.spec.values) if template is not None else None
            if tool == "select" and item.options and norm_label(value) not in {norm_label(o) for o in item.options}:
                return f"ERROR: option '{value}' not in {item.options}.", obs, None
            handoff.assert_in_control()
            try:
                surface.act(tool, item, value)
            except Exception as e:
                return f"ERROR: action failed: {type(e).__name__}", obs, None
            new_obs = self._settle(surface, obs if tool == "click" else None, 10000 if tool == "click" else 1500)
            det = ex.match_detector(new_obs)
            note = ""
            if det and det.classification == "recoverable" and det.recovery and det.recovery.kind == "click":
                try:
                    ex.handle_detector(det, new_obs, None)
                except HardFailure:
                    pass
                new_obs = self._settle(surface, new_obs)
                note = f" (the system dismissed a known '{det.id}' screen)"
            elif det and det.recovery and det.recovery.kind == "reauthenticate_and_restart":
                ex.authenticate()
                new_obs = self._settle(surface, None)
                return "Session expired; the system signed on again. Continue from the home screen.", new_obs, None
            trace.append(TraceEntry(action=tool, item=item, value=template, why=inp.get("why", ""), before=obs,
                                    after=new_obs))
            runlog.event("agent_action", None, tool=tool, target=f"{item.role} '{item.name}'", frame=item.frame,
                         value=self.log_redactor.text(value) if value else None, risk=risk,
                         state=det.id if det else None)
            state = f" Known state now showing: {det.id} ({det.classification})." if det and not note else ""
            return f"OK{note}.{state}", new_obs, None
        if tool == "extract":
            item = obs.by_ref(inp.get("ref", ""))
            if item is None:
                return f"ERROR: '{inp.get('ref')}' not in the latest observation.", obs, None
            spec = OutputSpec(name=inp["output_name"], type=inp["type"], description=inp.get("description", ""),
                              source=Target(kind=item.kind))
            raw = surface.read(item)
            try:
                parse_output(spec, raw)
            except ValueError:
                return f"ERROR: element {item.ref} does not contain a {inp['type']}.", obs, None
            extracts[:] = [e for e in extracts if e.name != spec.name]
            extracts.append(ExtractEntry(name=spec.name, type=spec.type, description=spec.description, item=item,
                                         obs=obs))
            runlog.event("agent_extract", None, output=spec.name, type=spec.type, frame=item.frame, ctx=item.ctx)
            return f"OK: '{spec.name}' will be extracted from {item.ref} (value withheld from you).", obs, None
        if tool == "done":
            want = norm_label(inp.get("success_text", ""))
            hit = next((i for i in obs.items if i.kind == "text" and want and want in norm_label(i.name)
                        and "[" not in self.model_redactor.redact_item(i.name, i.ctx)), None)
            if hit is None:
                return ("ERROR: success_text is not visible as unmasked text on the current screen; "
                        "use a heading or label you can see."), obs, None
            runlog.event("agent_done", None, success_text=hit.name, summary=inp.get("summary"))
            return "done", (obs, hit), "done"
        if tool == "request_human":
            if not self.allow_escalation:
                return f"stopped: model requested a human ({inp.get('reason')}) and escalation is disabled", obs, "stop"
            note = self._escalate(ex, handoff, trace, obs, inp.get("reason", "model requested help"))
            new_obs = self._settle(surface, None)
            return f"Human operator handed control back. {note}", new_obs, None
        if tool == "give_up":
            runlog.event("agent_give_up", None, reason=inp.get("reason"))
            return f"gave up: {inp.get('reason')}", obs, "give_up"
        return f"ERROR: unknown tool {tool}", obs, None

    def _escalate(self, ex: Executor, handoff, trace, obs: Observation, reason: str) -> str:
        resolution, actions = handoff.escalate(
            kind="stuck", reason=f"discovery: {reason}", step_id=None,
            context={"goal": self.spec.goal, "steps_so_far": len(trace), "reason": reason}, obs=obs)
        if resolution != "resume":
            raise HardFailure("ABORTED_BY_OPERATOR", f"operator resolution: {resolution}")
        after = ex.surface.observe()
        for a in actions:  # human clicks/selects become steps; redacted fills cannot be replayed
            t = json.loads(a["target_json"])
            if a["action"] == "fill" and a["value_redacted"] in ("[REDACTED]", "[SECRET]"):
                continue
            item = UIItem(ref="h", kind="control", role=t.get("role"), name=t.get("name") or "",
                          frame=t.get("frame") or "main", locator="", ctx=t.get("ctx") or {})
            trace.append(TraceEntry(action=a["action"], item=item, value=a["value_redacted"],
                                    why="performed by human operator", before=obs, after=after, origin="human"))
        return f"The operator performed {len(actions)} action(s)."
