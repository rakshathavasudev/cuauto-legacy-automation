"""Run evidence: structured, redacted, append-only.

evidence/runs/<started>_<kind>_<run_id>/
  events.jsonl       every decision and action, redacted (also mirrored to run_events)
  result.json        the caller-facing result, outputs redacted by declared sensitivity
  screens/*.png      screenshots with sensitive values blurred (on failure/escalation/end)
  snapshots/*.json   semantic UI snapshot at failure: what the surface saw, redacted
  transcript.jsonl   (discovery only) model turns, redacted, images omitted
Raw DOM is deliberately never persisted: it cannot be reliably redacted.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .safety.redaction import Redactor
from .storage.repo import Repo
from .surface.base import Observation
from .util import utcnow

log = logging.getLogger("cuauto")


class RunLog:
    def __init__(self, run_id: str, directory: Path, repo: Repo, redactor: Redactor, *, echo: bool = True):
        self.run_id = run_id
        self.dir = directory
        self.repo = repo
        self.redactor = redactor
        self.echo = echo
        self.seq = 0
        (self.dir / "screens").mkdir(parents=True, exist_ok=True)
        (self.dir / "snapshots").mkdir(exist_ok=True)
        self._events = open(self.dir / "events.jsonl", "a", encoding="utf-8")

    def event(self, type_: str, step_id: str | None = None, **payload: Any) -> None:
        self.seq += 1
        ts = utcnow()
        safe = self.redactor.obj(payload)
        rec = {"seq": self.seq, "ts": ts, "run_id": self.run_id, "type": type_, "step_id": step_id, **safe}
        self._events.write(json.dumps(rec, default=str) + "\n")
        self._events.flush()
        self.repo.add_event(self.run_id, self.seq, ts, type_, step_id, safe)
        if self.echo:
            brief = {k: v for k, v in safe.items() if k in ("intent", "reason", "code", "strategy", "detector",
                                                            "action", "status", "message", "resolution", "tool")}
            print(f"  [{self.seq:03d}] {type_:<22} {step_id or '':<6} {json.dumps(brief, default=str)[:160]}",
                  file=sys.stderr)

    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    def rel(self, p: Path | str) -> str:
        p = Path(p)
        try:
            return str(p.relative_to(self.dir.parent.parent.parent))
        except ValueError:
            return str(p)

    def snapshot(self, obs: Observation, label: str) -> str:
        items = [{"frame": i.frame, "kind": i.kind, "role": i.role,
                  "name": self.redactor.redact_item(i.name, i.ctx) if i.kind == "text" else self.redactor.text(i.name),
                  "ctx": {k: self.redactor.text(v) for k, v in i.ctx.items()}}
                 for i in obs.items]
        frames = [{"name": f.name, "url": self.redactor.text(f.url), "title": f.title} for f in obs.frames]
        p = self.path("snapshots", f"{self.seq:03d}_{label}.json")
        p.write_text(json.dumps({"frames": frames, "items": items}, indent=1))
        return self.rel(p)

    def write_json(self, name: str, data: Any) -> str:
        p = self.path(name)
        p.write_text(json.dumps(self.redactor.obj(data), indent=2, default=str))
        return self.rel(p)

    def close(self) -> None:
        self._events.close()
