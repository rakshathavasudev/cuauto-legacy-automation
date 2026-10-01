"""Operator actions on the *live* session (the mock co-browse surface).

Attaches over CDP to the browser the automation is driving — the same session, cookies
and page state — and performs operator actions by visible label. In production this is
replaced by a real remote-desktop/co-browse viewer; the control-transfer model is the same.
"""
from __future__ import annotations

from ..safety.policy import Policy
from ..schema.artifact import Target
from ..storage.repo import Repo
from ..surface.base import Resolution
from ..surface.web import WebSurface
from .operator import HandoffError, require_control


def parse_ops(clicks: list[str], fills: list[str], selects: list[str]) -> list[tuple[str, str, str | None]]:
    ops: list[tuple[str, str, str | None]] = []
    for f in fills:
        label, _, value = f.partition("=")
        ops.append(("fill", label.strip(), value))
    for s in selects:
        label, _, value = s.partition("=")
        ops.append(("select", label.strip(), value.strip()))
    ops += [("click", c.strip(), None) for c in clicks]
    return ops


def perform(repo: Repo, iid: str, operator: str, ops: list[tuple[str, str, str | None]],
            policy: Policy | None = None) -> list[str]:
    iv = require_control(repo, iid, operator)
    if not iv["session_endpoint"]:
        raise HandoffError("the run did not expose a live session endpoint")
    policy = policy or Policy(allowed_origins=[], allowed_paths=["/**"])
    surface = WebSurface(policy=policy, attach_endpoint=iv["session_endpoint"])
    surface.start()
    done = []
    try:
        for action, label, value in ops:
            role = {"fill": "textbox", "select": "combobox"}.get(action)
            res = surface.resolve(Target(role=role, name=label), {}, {})
            if not isinstance(res, Resolution) and action == "click":
                res = surface.resolve(Target(role="link", name=label), {}, {})
            if not isinstance(res, Resolution):
                raise HandoffError(f"'{label}': {res}")
            if action == "click" and res.item.role not in ("button", "link"):
                raise HandoffError(f"'{label}' is not clickable")
            surface.act(action, res.item, value)
            surface.pump(700)
            done.append(f"{action} '{label}'")
    finally:
        surface.close()
    return done


def live_screenshot(endpoint: str, sensitive_labels: list[str]) -> bytes:
    surface = WebSurface(policy=Policy(allowed_origins=[]), attach_endpoint=endpoint,
                         sensitive_labels=sensitive_labels)
    surface.start()
    try:
        return surface.observe(screenshot=True).screenshot_png or b""
    finally:
        surface.close()
