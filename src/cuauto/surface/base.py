"""The seam between *how we perceive/act on a surface* and *the recorded flow*.

Everything above this interface (agent loop, recorder, replay engine, artifact schema)
speaks only in `UIItem`s and semantic `Target`s. A surface adapter's whole job is:
  observe()  -> list of UIItems (role, name, context) in reading order
  resolve()  -> find the one item matching a Target (semantic first, fallbacks last)
  act()/read() on a resolved item
Adapters: WebSurface (Playwright, implemented). A desktop adapter would build the same
items from UIA/AX accessibility trees; a pixels-only adapter from screenshot + OCR/VLM
grounding, with `coordinates` fallbacks. See REPORT.md §4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..schema.artifact import Target
from ..util import norm_label


@dataclass
class UIItem:
    ref: str                      # ephemeral handle for this observation only (e3, t12)
    kind: str                     # control | text
    role: str | None
    name: str                     # accessible name for controls, text for text items
    frame: str                    # frame/window identifier
    locator: str                  # surface-specific ephemeral locator (xpath for web)
    ctx: dict[str, str] = field(default_factory=dict)
    value: str | None = None
    options: list[str] | None = None
    sensitive: bool = False
    disabled: bool = False
    emphasis: bool = False


@dataclass
class FrameState:
    name: str
    url: str
    title: str


@dataclass
class Observation:
    frames: list[FrameState]
    items: list[UIItem]
    screenshot_png: bytes | None = None

    def by_ref(self, ref: str) -> UIItem | None:
        return next((i for i in self.items if i.ref == ref), None)

    def texts(self, frame: str | None = None) -> list[str]:
        return [i.name for i in self.items if i.kind == "text" and (frame is None or i.frame == frame)]

    def all_visible_text(self) -> list[str]:
        return [i.name for i in self.items if i.name]

    def frame_url(self, name: str) -> str | None:
        return next((f.url for f in self.frames if f.name == name), None)

    def signature(self) -> str:
        return "|".join(f"{i.kind}:{i.role}:{i.name}:{i.frame}" for i in self.items)


@dataclass
class Resolution:
    item: UIItem
    strategy: str                 # semantic[+alias] | semantic_any_frame | *_nth | fallback:xpath
    drift: bool = False


class Surface(Protocol):
    session_endpoint: str | None

    def start(self) -> None: ...
    def close(self) -> None: ...
    def goto(self, url: str) -> None: ...
    def observe(self, *, screenshot: bool = False, redact_screenshot: bool = True) -> Observation: ...
    def resolve(self, target: Target, values: dict[str, str], aliases: dict[str, list[str]]) -> Resolution | str: ...
    def act(self, action: str, item: UIItem, value: str | None = None) -> None: ...
    def read(self, item: UIItem) -> str | None: ...
    def screenshot(self, path: str, *, redact: bool = True) -> str: ...
    def dom_snapshot(self) -> str: ...
    def pump(self, ms: int) -> None: ...
    def on_ui_event(self, cb: Callable[[dict[str, Any]], None]) -> None: ...


# ---- semantic matching shared by all adapters --------------------------------------------
def names_for(label: str | None, values: dict[str, str], aliases: dict[str, list[str]]) -> set[str]:
    from ..util import render
    if label is None:
        return set()
    rendered = render(label, values)
    out = {norm_label(rendered)}
    for key, alts in aliases.items():
        if norm_label(key) == norm_label(rendered):
            out |= {norm_label(a) for a in alts}
    return out


def matches(item: UIItem, target: Target, values: dict[str, str], aliases: dict[str, list[str]]) -> bool:
    if item.kind != target.kind:
        return False
    if target.kind == "control" and target.role and item.role != target.role:
        return False
    if target.name is not None and norm_label(item.name) not in names_for(target.name, values, aliases):
        return False
    c = target.context
    for key in ("row_label", "column_header", "field_label"):
        want = getattr(c, key)
        if want and norm_label(item.ctx.get(key)) not in names_for(want, values, aliases):
            return False
    return True


def resolve_in(items: list[UIItem], target: Target, values: dict[str, str],
               aliases: dict[str, list[str]]) -> Resolution | str:
    """Semantic resolution ladder. Returns a Resolution or a reason string.

    1. semantic match inside the hinted frame           (the normal case)
    2. semantic match in any frame                      (frame layout changed)
    3. nth among ambiguous semantic matches, if recorded
    Surface-specific fallbacks are tried by the adapter afterwards and flagged as drift.
    Never 'best guess': ambiguity without a recorded nth is a failure, not a coin flip.
    """
    from ..util import render as render_
    hint = target.context.frame
    in_hint = [i for i in items if (hint is None or i.frame == hint) and matches(i, target, values, aliases)]
    pools = [("semantic", in_hint)]
    if hint is not None:
        pools.append(("semantic_any_frame", [i for i in items if matches(i, target, values, aliases)]))
    for strategy, pool in pools:
        if len(pool) == 1:
            via_alias = (target.name is not None and
                         norm_label(pool[0].name) != norm_label(render_(target.name, values)))
            return Resolution(pool[0], strategy + ("+alias" if via_alias else ""), drift=strategy != "semantic")
        if len(pool) > 1:
            if target.nth is not None and target.nth < len(pool):
                return Resolution(pool[target.nth], f"{strategy}_nth")
            return f"ambiguous: {len(pool)} elements match {target.role} '{target.name}'"
    return "not found"
