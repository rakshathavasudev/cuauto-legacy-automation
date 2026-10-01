"""Minimal operator console (deliberately a mock of a real co-browse UI).

What is real: the intervention queue, the context the automation attached, the lease
(who holds the session), claim/release with fencing, a *live* screenshot pulled from the
same browser session over CDP, and acting on that session by visible label. Every human
action is captured by the automation side and stored in `human_actions`.
What is mocked: streaming video / pointer-level co-browsing (see REPORT.md §5).
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..runtime import open_repo
from ..settings import Settings
from . import act as act_mod
from .operator import HandoffError, claim, release

CSS = ("body{font-family:system-ui,sans-serif;margin:24px;max-width:1100px}table{border-collapse:collapse}"
       "td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}img{max-width:100%;border:1px solid #999}"
       "pre{background:#f5f5f5;padding:8px;white-space:pre-wrap}.err{color:#b00}form{display:inline-block;"
       "margin:4px 8px 4px 0}")


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><title>cuauto operator</title><style>{CSS}</style>{body}")


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="cuauto operator console")
    repo = open_repo(settings)
    e = html.escape

    @app.get("/")
    def index(show_all: int = 0):
        status = ("open", "claimed", "resolved", "expired") if show_all else ("open", "claimed")
        rows = "".join(
            f"<tr><td><a href='/i/{r['id']}'>{r['id']}</a></td><td>{r['kind']}</td><td>{r['status']}</td>"
            f"<td>{e(r['reason'])}</td><td>{(repo.get_lease(r['run_id']) or {}).get('holder')}</td>"
            f"<td>{r['created_at']}</td></tr>" for r in repo.list_interventions(status))
        return _page(f"<h2>Interventions</h2><p><a href='/?show_all=1'>show resolved</a></p><table><tr><th>id</th>"
                     f"<th>kind</th><th>status</th><th>reason</th><th>session holder</th><th>created</th></tr>"
                     f"{rows}</table><script>setTimeout(()=>location.reload(),3000)</script>")

    @app.get("/i/{iid}")
    def detail(iid: str, op: str = "operator", err: str = ""):
        iv = repo.get_intervention(iid)
        if not iv:
            return _page("<p>not found</p>")
        lease = repo.get_lease(iv["run_id"])
        ctx = json.dumps(json.loads(iv["context_json"]), indent=2)
        acts = "".join(f"<li>{a['ts']} {a['action']} {e(a['target_json'])} value={e(str(a['value_redacted']))}</li>"
                       for a in repo.human_actions(iid))
        holding = lease["holder"] == f"human:{op}"
        choices = ["resume", "abort"] if iv["kind"] == "stuck" else ["approve", "deny"]
        controls = ""
        if iv["status"] == "open":
            controls += (f"<form method=post action='/i/{iid}/claim'><input name=op value='{e(op)}'>"
                         f"<button>Take control</button></form>")
        if iv["status"] in ("open", "claimed"):
            controls += "".join(f"<form method=post action='/i/{iid}/release'><input type=hidden name=op "
                                f"value='{e(op)}'><button name=resolution value={c}>Hand back: {c}</button></form>"
                                for c in choices)
        live = ""
        if holding and iv["status"] == "claimed":
            live = (f"<h3>Live session (you are in control)</h3><img src='/i/{iid}/live.png?op={e(op)}'>"
                    f"<form method=post action='/i/{iid}/act'><input type=hidden name=op value='{e(op)}'>"
                    "<select name=action><option>click</option><option>fill</option><option>select</option>"
                    "</select> label <input name=label> value <input name=value><button>Do it</button></form>")
        return _page(
            f"<p><a href='/'>&larr; queue</a></p><h2>{iid} <small>({iv['kind']}, {iv['status']})</small></h2>"
            + (f"<p class=err>{e(err)}</p>" if err else "")
            + f"<p><b>Why it stopped:</b> {e(iv['reason'])}</p><p><b>Run:</b> {iv['run_id']} &nbsp; <b>Step:</b> "
            f"{iv['step_id']} &nbsp; <b>Session holder:</b> {lease['holder']} (epoch {lease['epoch']})</p>"
            f"{controls}{live}<h3>Context (redacted)</h3><pre>{e(ctx)}</pre>"
            f"<h3>Screenshot at escalation</h3><img src='/i/{iid}/screenshot.png'>"
            f"<h3>Recorded human actions</h3><ul>{acts or '<li>none yet</li>'}</ul>")

    def _back(iid: str, op: str, err: str = ""):
        return RedirectResponse(f"/i/{iid}?op={op}" + (f"&err={err}" if err else ""), status_code=303)

    @app.post("/i/{iid}/claim")
    def do_claim(iid: str, op: str = Form(...)):
        try:
            claim(repo, iid, op)
        except HandoffError as ex:
            return _back(iid, op, str(ex))
        return _back(iid, op)

    @app.post("/i/{iid}/release")
    def do_release(iid: str, op: str = Form(...), resolution: str = Form(...)):
        try:
            release(repo, iid, op, resolution, "via console")
        except HandoffError as ex:
            return _back(iid, op, str(ex))
        return _back(iid, op)

    @app.post("/i/{iid}/act")
    def do_act(iid: str, op: str = Form(...), action: str = Form(...), label: str = Form(...),
               value: str = Form("")):
        ops = act_mod.parse_ops([label] if action == "click" else [],
                                [f"{label}={value}"] if action == "fill" else [],
                                [f"{label}={value}"] if action == "select" else [])
        try:
            act_mod.perform(repo, iid, op, ops)
        except Exception as ex:
            return _back(iid, op, str(ex))
        return _back(iid, op)

    @app.get("/i/{iid}/screenshot.png")
    def shot(iid: str):
        iv = repo.get_intervention(iid)
        p = settings.home / iv["screenshot_path"] if iv and iv["screenshot_path"] else None
        if not p or not Path(p).exists():
            p = settings.evidence_dir.parent / iv["screenshot_path"] if iv else None
        return Response(Path(p).read_bytes() if p and Path(p).exists() else b"", media_type="image/png")

    @app.get("/i/{iid}/live.png")
    def live(iid: str, op: str):
        iv = repo.get_intervention(iid)
        lease = repo.get_lease(iv["run_id"]) if iv else None
        if not lease or lease["holder"] != f"human:{op}":
            return Response(status_code=403)
        return Response(act_mod.live_screenshot(iv["session_endpoint"], []), media_type="image/png")

    return app
