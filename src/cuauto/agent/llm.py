"""Model clients for the discovery loop.

AnthropicClient is the real one (tool use, one action per turn, optional screenshot).
ScriptedClient is an offline test double: it replays a fixed plan matched against the
semantic observation. It exists so tests and CI can exercise the loop and recorder
without network access. Artifacts it produces are stamped model='scripted-offline'
and must not be presented as a real discovery run.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..surface.base import Observation
from ..util import norm_label


@dataclass
class Decision:
    tool: str
    input: dict[str, Any]
    tool_use_id: str
    text: str = ""
    assistant_content: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient(Protocol):
    name: str

    def decide(self, system: str, messages: list[dict], tools: list[dict], obs: Observation) -> Decision: ...


class AnthropicClient:
    def __init__(self, model: str, api_key: str | None, max_tokens: int = 16000):
        import anthropic
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set (see .env.example)")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.name = model
        self.max_tokens = max_tokens

    def decide(self, system: str, messages: list[dict], tools: list[dict], obs: Observation) -> Decision:
        # forced tool_choice (any/tool) is rejected by current models; the system prompt requires a tool
        # call every turn, and disable_parallel_tool_use keeps it to at most one
        resp = self.client.messages.create(model=self.name, max_tokens=self.max_tokens, system=system,
                                           tools=tools, messages=messages,
                                           tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                                           thinking={"type": "adaptive", "display": "summarized"})
        content = [b.model_dump(exclude_none=True) for b in resp.content]
        # reasoning for the transcript: thinking summaries plus any text; adaptive thinking often skips
        # simple turns, so fall back to the rationale the model gives in the tool's `why` field
        text = " ".join([b.thinking for b in resp.content if b.type == "thinking" and b.thinking]
                        + [b.text for b in resp.content if b.type == "text"])
        tool = next((b for b in resp.content if b.type == "tool_use"), None)
        if tool is None:
            raise RuntimeError("model returned no tool call")
        text = text or str(tool.input.get("why", ""))
        # keep only the first tool_use so every tool_use in history gets exactly one tool_result
        first = [b for b in content if b.get("type") == "text"] + [next(b for b in content if b.get("type") == "tool_use")]
        return Decision(tool=tool.name, input=dict(tool.input), tool_use_id=tool.id, text=text,
                        assistant_content=first,
                        usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens})


class ScriptedClient:
    """Plan entries: {"tool": "click", "match": {"role": "link", "name": "View", "row_label": "10001"}, ...}."""

    def __init__(self, path: Path):
        self.plan = json.loads(Path(path).read_text())
        self.i = 0
        self.name = "scripted-offline"

    def _ref(self, obs: Observation, m: dict) -> str:
        for item in obs.items:
            if m.get("kind", "control" if "role" in m else "text") != item.kind:
                continue
            if "role" in m and item.role != m["role"]:
                continue
            if "name" in m and norm_label(item.name) != norm_label(m["name"]):
                continue
            if any(norm_label(item.ctx.get(k)) != norm_label(m[k]) for k in ("row_label", "column_header") if k in m):
                continue
            return item.ref
        raise RuntimeError(f"scripted plan step {self.i}: nothing matches {m}")

    def decide(self, system: str, messages: list[dict], tools: list[dict], obs: Observation) -> Decision:
        if self.i >= len(self.plan):
            entry = {"tool": "give_up", "input": {"reason": "script exhausted"}}
        else:
            entry = self.plan[self.i]
        self.i += 1
        inp = dict(entry.get("input", {}))
        if "match" in entry:
            inp["ref"] = self._ref(obs, entry["match"])
        tid = f"toolu_scripted_{self.i}"
        return Decision(tool=entry["tool"], input=inp, tool_use_id=tid, text=entry.get("why", ""),
                        assistant_content=[{"type": "tool_use", "id": tid, "name": entry["tool"], "input": inp}])


def image_block(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.b64encode(png).decode()}}
