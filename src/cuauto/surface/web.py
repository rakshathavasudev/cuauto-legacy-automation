"""Web surface adapter (Playwright/Chromium).

Handoff-relevant details:
  * The browser is launched with a CDP endpoint so an operator can attach to the *same
    live session* (DevTools, a remote viewer, or `cuauto operator act`).
  * A context-level binding receives UI events from the perception library; the engine
    decides whether they were human (lease held by a human) or automation.
  * Every network request passes through the policy allowlist (route guard).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from playwright.sync_api import Browser, BrowserContext, Frame, Page, Playwright, sync_playwright

from ..safety.policy import Policy, PolicyViolation
from ..schema.artifact import Target
from .base import FrameState, Observation, Resolution, UIItem, resolve_in
from .perception_js import LIB

log = logging.getLogger(__name__)

BLUR_PATTERNS = [r"\$\s?\d", r"\d{1,3}(,\d{3})*\.\d{2}", r"\d{3}-\d{2}-\d{4}", r"XXX-XX-\d{4}", r"\b\d{9,}\b"]


class WebSurface:
    def __init__(self, *, policy: Policy, headless: bool = True, cdp_port: int | None = None,
                 sensitive_labels: list[str] | None = None, attach_endpoint: str | None = None):
        self.policy = policy
        self.headless = headless
        self.cdp_port = cdp_port
        self.sensitive_labels = sensitive_labels or []
        self.attach_endpoint = attach_endpoint
        self.session_endpoint: str | None = None
        self.blocked_requests: list[str] = []
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self.page: Page | None = None
        self._event_cb: Callable[[dict[str, Any]], None] | None = None

    # -- lifecycle -------------------------------------------------------------------------------
    def start(self) -> None:
        self._pw = sync_playwright().start()
        if self.attach_endpoint:  # operator side: attach to an existing live session
            self._browser = self._pw.chromium.connect_over_cdp(self.attach_endpoint)
            self._ctx = self._browser.contexts[0]
            self.page = self._ctx.pages[0]
            self.session_endpoint = self.attach_endpoint
            return
        args = [f"--remote-debugging-port={self.cdp_port}"] if self.cdp_port else []
        self._browser = self._pw.chromium.launch(headless=self.headless, args=args)
        self._ctx = self._browser.new_context(viewport={"width": 1280, "height": 860})
        self._ctx.add_init_script(LIB)
        self._ctx.expose_binding("__cuautoRecord", self._on_binding)
        self._ctx.route("**/*", self._route_guard)
        self.page = self._ctx.new_page()
        self.session_endpoint = f"http://127.0.0.1:{self.cdp_port}" if self.cdp_port else None

    def close(self) -> None:
        try:
            if self._browser and not self.attach_endpoint:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def on_ui_event(self, cb: Callable[[dict[str, Any]], None]) -> None:
        self._event_cb = cb

    def _on_binding(self, source: dict, payload: dict) -> None:
        if self._event_cb:
            try:
                frame = source.get("frame")
                payload["frame"] = self._frame_name(frame) if frame else "top"
                self._event_cb(payload)
            except Exception:  # never let a recorder bug break the page
                log.exception("ui event callback failed")

    def _route_guard(self, route, request) -> None:
        d = self.policy.check_url(request.url)
        if d.allowed:
            route.continue_()
        else:
            self.blocked_requests.append(f"{request.url} ({d.reason})")
            route.abort("blockedbyclient")

    # -- navigation --------------------------------------------------------------------------------
    def goto(self, url: str) -> None:
        d = self.policy.check_url(url)
        if not d.allowed:
            raise PolicyViolation(d.reason)
        self.page.goto(url, wait_until="load")

    def pump(self, ms: int) -> None:
        """Wait while letting the browser deliver events (bindings, navigations)."""
        self.page.wait_for_timeout(ms)

    # -- perception ------------------------------------------------------------------------------------
    def _frame_name(self, f: Frame) -> str:
        if f == self.page.main_frame:
            return "top"
        return f.name or f.url.split("?")[0]

    def _eval(self, f: Frame, expr: str) -> Any:
        return f.evaluate(f"() => {{ {LIB}; return {expr}; }}")

    def observe(self, *, screenshot: bool = False, redact_screenshot: bool = True) -> Observation:
        frames: list[FrameState] = []
        items: list[UIItem] = []
        c = t = 0
        for f in self.page.frames:
            name = self._frame_name(f)
            try:
                inv = self._eval(f, "window.__cuauto.inventory(400)")
            except Exception:  # frame navigating mid-observation; next poll will catch it
                continue
            frames.append(FrameState(name=name, url=inv["url"], title=inv["title"]))
            for it in inv["items"]:
                if it["kind"] == "control":
                    c += 1
                    ref = f"e{c}"
                    name_ = it.get("name") or ""
                else:
                    t += 1
                    ref = f"t{t}"
                    name_ = it.get("text") or ""
                items.append(UIItem(ref=ref, kind=it["kind"], role=it.get("role"), name=name_, frame=name,
                                    locator=it["xpath"], ctx={k: v for k, v in (it.get("ctx") or {}).items() if v},
                                    value=it.get("value"), options=it.get("options"),
                                    sensitive=bool(it.get("sensitive")), disabled=bool(it.get("disabled")),
                                    emphasis=bool(it.get("emphasis"))))
        png = self._screenshot_bytes(redact_screenshot) if screenshot else None
        return Observation(frames=frames, items=items, screenshot_png=png)

    def _frame(self, name: str) -> Frame | None:
        return next((f for f in self.page.frames if self._frame_name(f) == name), None)

    # -- targeting -------------------------------------------------------------------------------------
    def resolve(self, target: Target, values: dict[str, str], aliases: dict[str, list[str]],
                obs: Observation | None = None) -> Resolution | str:
        obs = obs or self.observe()
        res = resolve_in(obs.items, target, values, aliases)
        if isinstance(res, Resolution):
            return res
        for fb in target.fallbacks:  # last resort, reported as drift
            if fb.strategy != "xpath":
                continue
            for item in obs.items:
                if item.locator == fb.value and (target.context.frame in (None, item.frame)):
                    return Resolution(item, "fallback:xpath", drift=True)
        return res

    def act(self, action: str, item: UIItem, value: str | None = None) -> None:
        f = self._frame(item.frame)
        if f is None:
            raise RuntimeError(f"frame '{item.frame}' disappeared")
        loc = f.locator(f"xpath={item.locator}")
        if action == "click":
            loc.click(timeout=5000)
        elif action == "fill":
            loc.fill(value or "", timeout=5000)
            loc.dispatch_event("change")
        elif action == "select":
            loc.select_option(label=value, timeout=5000)
        elif action == "press":
            loc.press(value or "Enter", timeout=5000)
        else:
            raise ValueError(f"unsupported action {action}")

    def read(self, item: UIItem) -> str | None:
        f = self._frame(item.frame)
        return self._eval(f, f"window.__cuauto.readText({item.locator!r})") if f else None

    # -- evidence ----------------------------------------------------------------------------------------
    def _set_blur(self, on: bool) -> None:
        for f in self.page.frames:
            try:
                self._eval(f, f"window.__cuauto.setBlur({BLUR_PATTERNS!r}, {self.sensitive_labels!r}, {str(on).lower()})")
            except Exception:
                pass

    def _screenshot_bytes(self, redact: bool) -> bytes:
        if redact:
            self._set_blur(True)
        try:
            return self.page.screenshot(full_page=False)
        finally:
            if redact:
                self._set_blur(False)

    def screenshot(self, path: str, *, redact: bool = True) -> str:
        with open(path, "wb") as fh:
            fh.write(self._screenshot_bytes(redact))
        return path

    def dom_snapshot(self) -> str:
        parts = []
        for f in self.page.frames:
            try:
                parts.append(f"<!-- frame: {self._frame_name(f)} {f.url} -->\n{f.content()}")
            except Exception:
                pass
        return "\n".join(parts)
