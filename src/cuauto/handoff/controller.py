"""Automation-side handoff: pause, cede control of the live session, resume.

Control-transfer model (REPORT.md §5):
  one lease row per run: holder_kind in {automation, human, none} + a fencing `epoch`
    automation --escalate--> none (awaiting operator) --claim--> human:<op>
    human:<op> --release(resume|abort|approve|deny)--> automation
  Every transfer is a compare-and-set on `epoch`, so two operators can't both claim,
  and the automation re-checks the lease before *every* action: if it does not hold the
  current epoch it stops acting. On timeout the automation never snatches control back
  from a human; the run fails with ESCALATION_TIMEOUT and the session stays with them.
"""
from __future__ import annotations

import sys
import time
from typing import Any

from ..evidence import RunLog
from ..schema.result import InterventionRecord
from ..storage.repo import Repo
from ..surface.base import Observation


class ControlLost(Exception):
    pass


class HandoffController:
    def __init__(self, repo: Repo, run_id: str, surface, runlog: RunLog, *, timeout_s: int = 600,
                 console_url: str | None = None):
        self.repo = repo
        self.run_id = run_id
        self.surface = surface
        self.runlog = runlog
        self.timeout_s = timeout_s
        self.console_url = console_url
        self.epoch = repo.init_lease(run_id)
        self.active: str | None = None
        self._last_event: tuple | None = None
        self.records: list[InterventionRecord] = []
        surface.on_ui_event(self._on_ui_event)

    # -- control checks ------------------------------------------------------------------------
    def assert_in_control(self) -> None:
        lease = self.repo.get_lease(self.run_id)
        if not lease or lease["holder_kind"] != "automation" or lease["epoch"] != self.epoch:
            raise ControlLost(f"lease is held by {lease and lease['holder']} (epoch {lease and lease['epoch']})")

    # -- human action capture ------------------------------------------------------------------
    def _on_ui_event(self, ev: dict[str, Any]) -> None:
        lease = self.repo.get_lease(self.run_id)
        if not self.active or not lease or lease["holder_kind"] != "human":
            return  # automation-originated event
        red = self.runlog.redactor
        value = ev.get("value")
        if ev.get("sensitive") or red.is_sensitive_context(ev.get("ctx")):
            value = "[REDACTED]" if value else value
        else:
            value = red.text(value) if isinstance(value, str) else value
        target = {"role": ev.get("role"), "name": red.text(ev.get("name")), "frame": ev.get("frame"),
                  "ctx": red.obj(ev.get("ctx") or {})}
        key = (self.active, ev.get("action"), target["name"], target["frame"], value)
        if key == self._last_event:  # input+change for the same edit arrive as two events
            return
        self._last_event = key
        self.repo.add_human_action(intervention_id=self.active, run_id=self.run_id, action=ev.get("action", "?"),
                                   target=target, value_redacted=value, frame_url=red.text(ev.get("url")))
        self.runlog.event("human_action", None, action=ev.get("action"), target=target, value=value,
                          operator=lease["holder"])

    # -- escalation ------------------------------------------------------------------------------
    def escalate(self, *, kind: str, reason: str, step_id: str | None, context: dict[str, Any],
                 obs: Observation | None = None) -> tuple[str, list[dict]]:
        shot = self.surface.screenshot(str(self.runlog.path("screens", f"{self.runlog.seq + 1:03d}_escalation.png")))
        snap = self.runlog.snapshot(obs, "escalation") if obs else None
        ctx = self.runlog.redactor.obj({**context, "snapshot": snap})
        iid = self.repo.create_intervention(run_id=self.run_id, kind=kind, reason=reason, step_id=step_id,
                                            context=ctx, screenshot_path=self.runlog.rel(shot),
                                            session_endpoint=self.surface.session_endpoint)
        new_epoch = self.repo.transfer_lease(self.run_id, expected_epoch=self.epoch, holder_kind="none",
                                             holder="awaiting_operator")
        if new_epoch is None:
            raise ControlLost("could not cede control: lease changed concurrently")
        self.epoch = new_epoch
        self.active = iid
        self.repo.set_run_status(self.run_id, "paused")
        self.runlog.event("intervention_opened", step_id, intervention_id=iid, kind=kind, reason=reason,
                          screenshot=self.runlog.rel(shot), session_endpoint=self.surface.session_endpoint)
        print(f"\n  >>> HUMAN INTERVENTION REQUIRED [{kind}] {iid}\n      reason: {reason}\n"
              f"      claim:  cuauto operator claim {iid} --as <you>\n"
              f"      live session (CDP): {self.surface.session_endpoint}"
              + (f"\n      console: {self.console_url}" if self.console_url else "") + "\n", file=sys.stderr)

        deadline = time.monotonic() + self.timeout_s
        row = self.repo.get_intervention(iid)
        while row["status"] in ("open", "claimed") and time.monotonic() < deadline:
            self.surface.pump(400)  # keeps the page alive and delivers human-action events
            row = self.repo.get_intervention(iid)
        if row["status"] in ("open", "claimed"):
            self.repo.resolve_intervention(iid, "timeout", "no operator response", status="expired")
            row = self.repo.get_intervention(iid)
            lease = self.repo.get_lease(self.run_id)
            if lease["holder_kind"] == "none":  # nobody took over: safe to take the session back
                self.epoch = self.repo.transfer_lease(self.run_id, expected_epoch=lease["epoch"],
                                                      holder_kind="automation", holder="automation") or self.epoch
        self.surface.pump(300)  # flush trailing events
        lease = self.repo.get_lease(self.run_id)
        if lease["holder_kind"] == "automation":
            self.epoch = lease["epoch"]
            self.repo.set_run_status(self.run_id, "running")
        actions = self.repo.human_actions(iid)
        self.records.append(InterventionRecord(id=iid, kind=kind, reason=reason, resolution=row["resolution"],
                                               operator=row["claimed_by"], human_actions=len(actions)))
        self.runlog.event("intervention_closed", step_id, intervention_id=iid, resolution=row["resolution"],
                          operator=row["claimed_by"], note=row["resolution_note"], human_actions=len(actions))
        self.active = None
        return row["resolution"], actions
