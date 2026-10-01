"""Legacy CoreBank 4.2 — a deliberately hostile stand-in for a credit-union teller UI.

Properties chosen to mirror the real environment, not a demo site:
  * frameset layout (nav frame + main frame), table-based layout, <font> tags
  * no ids, no test-ids, no <label for>, opaque field names (p_mbr, f1, f2)
  * CGI-style routes and a random per-request token in URLs
  * realistic runtime exceptional states, injectable at runtime via /_admin/faults

Faults (set with /_admin/faults?set=name[@/path/prefix],... ; clear with ?clear=1):
  slow                  every /cgi request sleeps 2.5s (transient slowness)
  error                 every /cgi request returns a 500 "ABEND" page (hard failure)
  interstitial_once     next matching GET shows a SYSTEM NOTICE page with Continue
  session_expire_once   next matching request finds the session expired
  override_once         next matching GET demands a supervisor override code (unknown state)

Data: member 10001/10002 exist, 55555 is restricted, other numbers are not found,
non-numeric input fails validation. Nothing here is real PII.
"""
from __future__ import annotations

import asyncio
import html
import os
import secrets

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

VARIANT = os.environ.get("MOCK_VARIANT", "a")
L = {
    "a": dict(brand="Buffalo Teachers FCU", member_no="Member Number", search="Search",
              open_sub="Open Sub-Account", inquiry="Member Inquiry"),
    # Same vendor product, different tenant configuration/branding.
    "b": dict(brand="Lakeshore Community CU", member_no="Member #", search="Find",
              open_sub="New Share", inquiry="Member Lookup"),
}[VARIANT]

USER = os.environ.get("MOCK_BANK_USER", "teller1")
PASSWORD = os.environ.get("MOCK_BANK_PASSWORD", "demo-pass-123")
OVERRIDE_CODE = "2468"

MEMBERS = {
    "10001": {"name": "Jordan A. Rivera", "since": "03/14/2011", "ssn": "XXX-XX-4821",
              "shares": [("S00", "Primary Savings", "1,234.56", "1,229.56"),
                         ("S10", "Share Draft Checking", "842.10", "842.10"),
                         ("S20", "Holiday Club", "300.00", "0.00")]},
    "10002": {"name": "Casey M. Okafor", "since": "08/02/2019", "ssn": "XXX-XX-1177",
              "shares": [("S00", "Primary Savings", "25.00", "20.00")]},
    "55555": {"restricted": True, "name": "RESTRICTED", "since": "", "ssn": "", "shares": []},
}

SESSIONS: set[str] = set()
FAULTS: dict[str, str] = {}  # name -> path prefix
NEXT_SHARE = {"n": 30}

app = FastAPI(title="Legacy CoreBank 4.2 (mock)")


def page(body: str, title: str = "CoreBank 4.2") -> HTMLResponse:
    return HTMLResponse(
        f"<html><head><title>{title}</title></head>"
        f"<body bgcolor='#C0C0C0' style='margin:6px'><font face='Arial' size='2'>{body}</font></body></html>"
    )


def banner(text: str) -> str:
    return (f"<table width='100%' bgcolor='#000080' cellpadding='3'><tr><td>"
            f"<font color='white'><b>{text}</b></font></td></tr></table><br>")


def authed(request: Request) -> bool:
    return request.cookies.get("CBSESS") in SESSIONS


def fault_active(name: str, path: str) -> bool:
    prefix = FAULTS.get(name)
    return prefix is not None and path.startswith(prefix or "/cgi/")


@app.middleware("http")
async def inject_faults(request: Request, call_next):
    path = request.url.path
    if path.startswith("/cgi/") and path not in ("/cgi/ack", "/cgi/override"):
        if fault_active("slow", path):
            await asyncio.sleep(2.5)
        if fault_active("error", path):
            return HTMLResponse(page(banner("SYSTEM ERROR") +
                                     "<p>AN UNEXPECTED ERROR HAS OCCURRED. ABEND S0C7 IN MODULE CBMBR210."
                                     "<br>CONTACT THE HELP DESK.</p>").body, status_code=500)
        if fault_active("session_expire_once", path):
            FAULTS.pop("session_expire_once")
            SESSIONS.clear()
        nxt = html.escape(str(request.url.path) + ("?" + request.url.query if request.url.query else ""), quote=True)
        if request.method == "GET" and fault_active("interstitial_once", path):
            FAULTS.pop("interstitial_once")
            return page(banner("SYSTEM NOTICE") +
                        "<p>End-of-day batch posting begins at 18:00 ET. Items entered after that time "
                        "post on the next business day.</p>"
                        f"<form method='get' action='/cgi/ack'><input type='hidden' name='next' value='{nxt}'>"
                        "<input type='submit' value='Continue'></form>")
        if request.method == "GET" and fault_active("override_once", path):
            FAULTS.pop("override_once")
            return page(banner("SUPERVISOR OVERRIDE REQUIRED") +
                        "<p>This inquiry requires a supervisor override.</p>"
                        f"<form method='post' action='/cgi/override'><input type='hidden' name='next' value='{nxt}'>"
                        "<table><tr><td>Override Code:</td><td><input type='password' name='oc' size='8'></td></tr>"
                        "</table><input type='submit' value='Authorize'></form>")
    return await call_next(request)


def expired() -> HTMLResponse:
    return page(banner("SIGN ON REQUIRED") +
                "<p>YOUR SESSION HAS EXPIRED. PLEASE SIGN ON AGAIN.</p>"
                "<a href='/login' target='_top'>Sign On</a>")


# --------------------------------------------------------------------------- auth
@app.get("/login")
def login_form(msg: str = "") -> HTMLResponse:
    err = f"<p><font color='red'><b>{html.escape(msg)}</b></font></p>" if msg else ""
    return page(banner(f"{L['brand']} - CoreBank Teller Sign On") + err +
                "<form method='post' action='/login'><table border='0'>"
                "<tr><td>User ID:</td><td><input type='text' name='f1' size='12'></td></tr>"
                "<tr><td>Password:</td><td><input type='password' name='f2' size='12'></td></tr>"
                "</table><input type='submit' value='Sign On'></form>")


@app.post("/login")
def login(f1: str = Form(""), f2: str = Form("")):
    if f1 != USER or f2 != PASSWORD:
        return RedirectResponse("/login?msg=INVALID USER ID OR PASSWORD", status_code=303)
    sid = secrets.token_hex(8)
    SESSIONS.add(sid)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie("CBSESS", sid, httponly=True)
    return r


@app.get("/")
def root(request: Request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse("<html><head><title>CoreBank 4.2</title></head>"
                        "<frameset rows='44,*' border='1'>"
                        "<frame name='nav' src='/nav' scrolling='no'>"
                        "<frame name='main' src='/cgi/home'></frameset></html>")


@app.get("/nav")
def nav() -> HTMLResponse:
    return page(f"<table width='100%'><tr><td><b>{L['brand']}</b></td>"
                f"<td><a href='/cgi/mbrinq' target='main'>{L['inquiry']}</a></td>"
                "<td><a href='/logout' target='_top'>Sign Off</a></td></tr></table>")


@app.get("/logout")
def logout(request: Request):
    SESSIONS.discard(request.cookies.get("CBSESS", ""))
    return RedirectResponse("/login", status_code=303)


# --------------------------------------------------------------------------- cgi
@app.get("/cgi/home")
def home(request: Request):
    if not authed(request):
        return expired()
    return page(banner("TELLER WORKSTATION") + "<p>Select a function from the menu above.</p>")


@app.get("/cgi/ack")
def ack(next: str = "/cgi/home"):
    return RedirectResponse(next if next.startswith("/cgi/") else "/cgi/home", status_code=303)


@app.post("/cgi/override")
def override(oc: str = Form(""), next: str = Form("/cgi/home")):
    if oc != OVERRIDE_CODE:
        return page(banner("SUPERVISOR OVERRIDE REQUIRED") + "<p><font color='red'>OVERRIDE REJECTED</font></p>")
    return RedirectResponse(next if next.startswith("/cgi/") else "/cgi/home", status_code=303)


def inquiry_form(err: str = "") -> HTMLResponse:
    e = f"<p><font color='red'><b>{err}</b></font></p>" if err else ""
    return page(banner("MEMBER INQUIRY") + e +
                "<form method='get' action='/cgi/mbrsrch'><table>"
                f"<tr><td>{L['member_no']}:</td><td><input type='text' name='p_mbr' size='10'></td></tr>"
                f"</table><input type='submit' value='{L['search']}'></form>")


@app.get("/cgi/mbrinq")
def mbrinq(request: Request):
    return inquiry_form() if authed(request) else expired()


@app.get("/cgi/mbrsrch")
def mbrsrch(request: Request, p_mbr: str = ""):
    if not authed(request):
        return expired()
    p = p_mbr.strip()
    if not (p.isdigit() and 5 <= len(p) <= 8):
        return inquiry_form("INVALID MEMBER NUMBER - MUST BE 5 TO 8 DIGITS")
    m = MEMBERS.get(p)
    if not m:
        return page(banner("MEMBER SEARCH RESULTS") +
                    f"<p><b>NO MEMBER FOUND FOR NUMBER {html.escape(p)}</b></p>"
                    "<a href='/cgi/mbrinq'>New Inquiry</a>")
    tok = secrets.token_hex(3)
    name = m["name"].upper()
    status = "RESTRICTED" if m.get("restricted") else "ACTIVE"
    return page(banner("MEMBER SEARCH RESULTS") +
                "<table border='1' cellpadding='3'><tr><th>Member No</th><th>Name</th><th>Status</th><th></th></tr>"
                f"<tr><td>{p}</td><td>{html.escape(name)}</td><td>{status}</td>"
                f"<td><a href='/cgi/mbrdtl?m={p}&tok={tok}'>View</a></td></tr></table>")


@app.get("/cgi/mbrdtl")
def mbrdtl(request: Request, m: str = ""):
    if not authed(request):
        return expired()
    mem = MEMBERS.get(m)
    if not mem:
        return page(banner("MEMBER ACCOUNT SUMMARY") + "<p><b>NO MEMBER FOUND</b></p>")
    if mem.get("restricted"):
        return page(banner("ACCESS DENIED") +
                    "<p>YOU ARE NOT AUTHORIZED TO VIEW THIS RECORD (RC=403).</p>")
    rows = "".join(f"<tr><td>{s}</td><td>{d}</td><td align='right'>${b}</td><td align='right'>${a}</td></tr>"
                   for s, d, b, a in mem["shares"])
    return page(banner("MEMBER ACCOUNT SUMMARY") +
                "<table cellpadding='2'>"
                f"<tr><td>Member Name:</td><td>{html.escape(mem['name'])}</td></tr>"
                f"<tr><td>Member Since:</td><td>{mem['since']}</td></tr>"
                f"<tr><td>SSN:</td><td>{mem['ssn']}</td></tr></table><br>"
                "<table border='1' cellpadding='3'><tr><th>Share</th><th>Description</th>"
                f"<th>Current Balance</th><th>Available</th></tr>{rows}</table><br>"
                f"<form method='get' action='/cgi/subacct/new'><input type='hidden' name='m' value='{m}'>"
                f"<input type='submit' value='{L['open_sub']}'></form>")


@app.get("/cgi/subacct/new")
def subacct_new(request: Request, m: str = "", err: str = ""):
    if not authed(request):
        return expired()
    e = f"<p><font color='red'><b>{html.escape(err)}</b></font></p>" if err else ""
    return page(banner("OPEN SUB-ACCOUNT") + e +
                f"<form method='post' action='/cgi/subacct/review'><input type='hidden' name='m' value='{m}'>"
                "<table><tr><td>Share Type:</td><td><select name='st'><option value=''>-- select --</option>"
                "<option value='XC'>Christmas Club</option><option value='VC'>Vacation Club</option>"
                "<option value='SC'>Savings Club</option></select></td></tr>"
                "<tr><td>Nickname:</td><td><input type='text' name='nk' size='20'></td></tr>"
                "<tr><td>Opening Deposit:</td><td><input type='text' name='dp' size='10'></td></tr>"
                "</table><input type='submit' value='Continue'></form>")


@app.post("/cgi/subacct/review")
def subacct_review(request: Request, m: str = Form(""), st: str = Form(""), nk: str = Form(""),
                   dp: str = Form("")):
    if not authed(request):
        return expired()
    try:
        amount = float(dp.replace(",", "").replace("$", ""))
    except ValueError:
        amount = -1
    if not st or amount < 5:
        return RedirectResponse(f"/cgi/subacct/new?m={m}&err=INVALID ENTRY - SHARE TYPE REQUIRED, "
                                "MINIMUM DEPOSIT 5.00", status_code=303)
    types = {"XC": "Christmas Club", "VC": "Vacation Club", "SC": "Savings Club"}
    return page(banner("REVIEW NEW SUB-ACCOUNT") +
                "<table border='1' cellpadding='3'>"
                f"<tr><td>Share Type</td><td>{types.get(st, st)}</td></tr>"
                f"<tr><td>Nickname</td><td>{html.escape(nk)}</td></tr>"
                f"<tr><td>Opening Deposit</td><td>${amount:,.2f}</td></tr></table>"
                "<p>Please review. Selecting Confirm and Open will create the share and post the deposit.</p>"
                f"<form method='post' action='/cgi/subacct/commit'><input type='hidden' name='m' value='{m}'>"
                f"<input type='hidden' name='st' value='{st}'><input type='hidden' name='dp' value='{amount}'>"
                "<input type='submit' value='Confirm and Open'></form>"
                f"<form method='get' action='/cgi/mbrdtl'><input type='hidden' name='m' value='{m}'>"
                "<input type='submit' value='Cancel'></form>")


@app.post("/cgi/subacct/commit")
def subacct_commit(request: Request, m: str = Form(""), st: str = Form("")):
    if not authed(request):
        return expired()
    NEXT_SHARE["n"] += 1
    return page(banner("SUB-ACCOUNT OPENED") + f"<p>New share S{NEXT_SHARE['n']} opened.</p>")


# --------------------------------------------------------------------------- admin (not agent-reachable)
@app.get("/_admin/faults")
def admin_faults(set: str = "", clear: int = 0):
    if clear:
        FAULTS.clear()
    for spec in filter(None, (s.strip() for s in set.split(","))):
        name, _, prefix = spec.partition("@")
        FAULTS[name] = prefix
    return JSONResponse({"faults": FAULTS, "variant": VARIANT})
