"""`cuauto` command line.

Exit codes for `replay` / `invoke` mirror the result contract so shell callers can branch:
  0 success   2 business_outcome   1 failed   3 rejected (contract check, UI never touched)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from .runtime import ConfigError, load_environment, open_repo
from .settings import load_settings

EXIT = {"success": 0, "business_outcome": 2, "failed": 1, "rejected": 3}


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _kv(pairs: list[str]) -> dict[str, str]:
    args = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--arg expects name=value, got '{p}'")
        k, _, v = p.partition("=")
        args[k.strip()] = v
    return args


# --- db / mock ----------------------------------------------------------------------------------------
def cmd_db_init(a, s) -> int:
    from .storage.db import connect, migrate
    applied = migrate(connect(s.db_path))
    _out({"db": str(s.db_path), "applied": applied})
    return 0


def cmd_mock_bank(a, s) -> int:
    env = {**os.environ, "MOCK_VARIANT": a.variant}
    return subprocess.call([sys.executable, "-m", "uvicorn", "mock_bank.app:app", "--host", "127.0.0.1",
                            "--port", str(a.port), "--log-level", "warning"], env=env, cwd=str(s.home))


def cmd_faults(a, s) -> int:
    env = load_environment(s, a.tenant, need_secrets=False)
    q = "clear=1" if a.clear else "set=" + ",".join(a.set or [])
    with urllib.request.urlopen(env.tenant.base_url.rstrip("/") + "/_admin/faults?" + q, timeout=5) as r:
        print(r.read().decode())
    return 0


# --- discovery ------------------------------------------------------------------------------------------
def cmd_discover(a, s) -> int:
    from .agent.llm import AnthropicClient, ScriptedClient
    from .agent.loop import DiscoveryAgent, load_goal
    env = load_environment(s, a.tenant)
    spec = load_goal(Path(a.goal_file))
    if a.llm == "anthropic":
        llm = AnthropicClient(a.model or s.model, s.anthropic_api_key)
    elif a.llm.startswith("scripted:"):
        llm = ScriptedClient(Path(a.llm.split(":", 1)[1]))
    else:
        raise SystemExit("--llm must be 'anthropic' or 'scripted:<plan.json>'")
    agent = DiscoveryAgent(env, llm, spec, max_steps=a.max_steps, timeout_s=a.timeout, vision=not a.no_vision,
                           headed=a.headed, allow_escalation=not a.no_escalation,
                           escalation_timeout_s=a.escalation_timeout)
    r = agent.run()
    _out({"run_id": r.run_id, "status": r.status, "message": r.message, "steps": r.steps_taken, "usage": r.usage,
          "evidence_dir": r.evidence_dir,
          "capability": r.capability and {"id": r.capability.id, "version": r.capability.version,
                                           "registry_status": "draft"}})
    return 0 if r.status == "success" else 1


# --- capabilities ----------------------------------------------------------------------------------------
def cmd_cap_list(a, s) -> int:
    repo = open_repo(s)
    rows = repo.list_capabilities()
    if a.json:
        _out(rows)
        return 0
    print(f"{'CAPABILITY':<45} {'VER':>3}  {'STATUS':<10} {'SHA256':<14} ARTIFACT")
    for r in rows:
        print(f"{r['id']:<45} {r['version']:>3}  {r['status']:<10} {r['content_sha256'][:12]:<14} {r['artifact_path']}")
    return 0


def _store(s):
    from .storage.artifacts import ArtifactStore
    repo = open_repo(s)
    return repo, ArtifactStore(s.capabilities_dir, repo)


def cmd_cap_show(a, s) -> int:
    _, store = _store(s)
    cap, row = store.load(a.capability, a.version)
    print(f"# registry: status={row['status']} approved_by={row['approved_by']} sha256={row['content_sha256']}")
    print(cap.to_yaml() if not a.tool_schema else json.dumps(cap.tool_schema(), indent=2))
    return 0


def cmd_cap_status(a, s, status: str) -> int:
    repo, store = _store(s)
    cap, row = store.load(a.capability, a.version)  # verifies hash before approving
    if status == "approved" and cap.provenance.model == "scripted-offline" and not a.force:
        raise SystemExit("refusing to approve an artifact produced by the offline scripted client (use --force)")
    repo.set_version_status(cap.id, cap.version, status, a.by)
    _out({"capability": cap.id, "version": cap.version, "status": status, "by": a.by})
    return 0


# --- replay / catalog / invoke ------------------------------------------------------------------------------
def _replay(s, tenant: str, cap_id: str, version, args: dict, a) -> int:
    from .replay.engine import ReplayEngine
    env = load_environment(s, tenant)
    cap, row = env.store.load(cap_id, version)
    eng = ReplayEngine(env, cap, row, on_stuck=a.on_stuck, confirm_irreversible=a.confirm_irreversible,
                       allow_draft=a.allow_draft, headed=a.headed, escalation_timeout_s=a.escalation_timeout)
    result = eng.run(args)
    # The caller (the AI agent) receives real output values; only persisted copies are redacted.
    _out(result.model_dump(mode="json", exclude_none=True))
    return EXIT[result.status.value]


def cmd_replay(a, s) -> int:
    return _replay(s, a.tenant, a.capability, a.version, _kv(a.arg), a)


def cmd_catalog(a, s) -> int:
    repo, store = _store(s)
    tools, seen = [], set()
    for r in repo.list_capabilities():
        if r["id"] in seen:
            continue
        row = repo.get_version(r["id"])
        if row and (row["status"] == "approved" or a.include_drafts):
            seen.add(r["id"])
            cap, _ = store.load(r["id"], row["version"])
            tools.append({**cap.tool_schema(), "x-capability": {"id": cap.id, "version": cap.version,
                                                                 "status": row["status"]}})
    _out(tools)
    return 0


def cmd_invoke(a, s) -> int:
    """Agent-facing entry: invoke by tool name with JSON args, exactly as a function call arrives."""
    repo = open_repo(s)
    cap_id = a.tool.replace("__", ".")
    if repo.get_version(cap_id) is None:
        raise SystemExit(f"no capability for tool '{a.tool}' (see `cuauto catalog`)")
    return _replay(s, a.tenant, cap_id, None, {k: str(v) for k, v in json.loads(a.args).items()}, a)


# --- operator (handoff) ---------------------------------------------------------------------------------------
def cmd_op_list(a, s) -> int:
    repo = open_repo(s)
    rows = repo.list_interventions(("open", "claimed") if not a.all else
                                   ("open", "claimed", "resolved", "expired"))
    for r in rows:
        lease = repo.get_lease(r["run_id"]) or {}
        print(f"{r['id']}  {r['kind']:<8} {r['status']:<8} run={r['run_id']} holder={lease.get('holder')} "
              f"epoch={lease.get('epoch')}\n    reason: {r['reason']}")
    if not rows:
        print("(no active interventions)")
    return 0


def cmd_op_show(a, s) -> int:
    repo = open_repo(s)
    iv = repo.get_intervention(a.id)
    if not iv:
        raise SystemExit("no such intervention")
    iv["context"] = json.loads(iv.pop("context_json"))
    iv["lease"] = repo.get_lease(iv["run_id"])
    iv["human_actions"] = repo.human_actions(a.id)
    _out(iv)
    return 0


def cmd_op_claim(a, s) -> int:
    from .handoff.operator import claim
    repo = open_repo(s)
    epoch = claim(repo, a.id, a.as_)
    iv = repo.get_intervention(a.id)
    _out({"claimed": a.id, "holder": f"human:{a.as_}", "epoch": epoch, "session_endpoint": iv["session_endpoint"],
          "next": f"cuauto operator act {a.id} --as {a.as_} --click '<label>'  |  "
                  f"cuauto operator release {a.id} --as {a.as_} --resolution resume"})
    return 0


def cmd_op_release(a, s) -> int:
    from .handoff.operator import release
    epoch = release(open_repo(s), a.id, a.as_, a.resolution, a.note)
    _out({"released": a.id, "resolution": a.resolution, "holder": "automation", "epoch": epoch})
    return 0


def cmd_op_act(a, s) -> int:
    from .handoff.act import parse_ops, perform
    done = perform(open_repo(s), a.id, a.as_, parse_ops(a.click or [], a.fill or [], a.select or []))
    _out({"performed": done})
    return 0


def cmd_op_console(a, s) -> int:
    import uvicorn
    from .handoff.console import create_app
    uvicorn.run(create_app(s), host="127.0.0.1", port=a.port, log_level="warning")
    return 0


# --- parser -----------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cuauto", description="Computer-use discovery -> capability -> replay")
    sub = p.add_subparsers(dest="cmd", required=True)

    db = sub.add_parser("db").add_subparsers(dest="sub", required=True)
    db.add_parser("init").set_defaults(fn=cmd_db_init)

    m = sub.add_parser("mock-bank", help="run the mock legacy core-banking app")
    m.add_argument("--port", type=int, default=8401)
    m.add_argument("--variant", choices=["a", "b"], default="a")
    m.set_defaults(fn=cmd_mock_bank)

    f = sub.add_parser("faults", help="inject runtime faults into a mock tenant")
    f.add_argument("--tenant", required=True)
    f.add_argument("--set", nargs="*", help="name[@/path/prefix] ... e.g. interstitial_once@/cgi/mbrdtl")
    f.add_argument("--clear", action="store_true")
    f.set_defaults(fn=cmd_faults)

    d = sub.add_parser("discover", help="LLM-driven discovery run that records a capability")
    d.add_argument("--tenant", required=True)
    d.add_argument("--goal-file", required=True)
    d.add_argument("--llm", default="anthropic", help="anthropic | scripted:<plan.json> (offline test double)")
    d.add_argument("--model", default=None)
    d.add_argument("--max-steps", type=int, default=25)
    d.add_argument("--timeout", type=int, default=300)
    d.add_argument("--escalation-timeout", type=int, default=600)
    d.add_argument("--no-vision", action="store_true", help="semantic view only, no screenshots to the model")
    d.add_argument("--no-escalation", action="store_true")
    d.add_argument("--headed", action="store_true")
    d.set_defaults(fn=cmd_discover)

    c = sub.add_parser("capabilities", aliases=["cap"]).add_subparsers(dest="sub", required=True)
    cl = c.add_parser("list")
    cl.add_argument("--json", action="store_true")
    cl.set_defaults(fn=cmd_cap_list)
    cs = c.add_parser("show")
    cs.add_argument("capability")
    cs.add_argument("--version", type=int)
    cs.add_argument("--tool-schema", action="store_true")
    cs.set_defaults(fn=cmd_cap_show)
    for name, status in (("approve", "approved"), ("deprecate", "deprecated")):
        cp = c.add_parser(name)
        cp.add_argument("capability")
        cp.add_argument("--version", type=int, required=True)
        cp.add_argument("--by", required=True)
        cp.add_argument("--force", action="store_true")
        cp.set_defaults(fn=lambda a, s, st=status: cmd_cap_status(a, s, st))

    def replay_opts(x):
        x.add_argument("--tenant", required=True)
        x.add_argument("--on-stuck", choices=["fail", "escalate"], default="fail")
        x.add_argument("--confirm-irreversible", action="store_true")
        x.add_argument("--allow-draft", action="store_true", help="supervised run of an unapproved version")
        x.add_argument("--escalation-timeout", type=int, default=600)
        x.add_argument("--headed", action="store_true")

    r = sub.add_parser("replay", help="deterministic replay (no LLM)")
    r.add_argument("capability")
    r.add_argument("--version", type=int)
    r.add_argument("--arg", action="append", help="name=value (repeatable)")
    replay_opts(r)
    r.set_defaults(fn=cmd_replay)

    cat = sub.add_parser("catalog", help="agent-facing tool definitions of approved capabilities")
    cat.add_argument("--include-drafts", action="store_true")
    cat.set_defaults(fn=cmd_catalog)

    inv = sub.add_parser("invoke", help="invoke a capability by tool name with JSON args")
    inv.add_argument("tool")
    inv.add_argument("--args", required=True, help='JSON object, e.g. \'{"member_id": "10001"}\'')
    replay_opts(inv)
    inv.set_defaults(fn=cmd_invoke)

    o = sub.add_parser("operator", help="human-in-the-loop handoff").add_subparsers(dest="sub", required=True)
    ol = o.add_parser("list")
    ol.add_argument("--all", action="store_true")
    ol.set_defaults(fn=cmd_op_list)
    osh = o.add_parser("show")
    osh.add_argument("id")
    osh.set_defaults(fn=cmd_op_show)
    oc = o.add_parser("claim", help="take control of the live session")
    oc.add_argument("id")
    oc.add_argument("--as", dest="as_", required=True)
    oc.set_defaults(fn=cmd_op_claim)
    orl = o.add_parser("release", help="hand control back with a resolution")
    orl.add_argument("id")
    orl.add_argument("--as", dest="as_", required=True)
    orl.add_argument("--resolution", required=True, choices=["resume", "abort", "approve", "deny"])
    orl.add_argument("--note")
    orl.set_defaults(fn=cmd_op_release)
    oa = o.add_parser("act", help="act on the live session (mock co-browse)")
    oa.add_argument("id")
    oa.add_argument("--as", dest="as_", required=True)
    oa.add_argument("--click", action="append")
    oa.add_argument("--fill", action="append", help="'Label=value'")
    oa.add_argument("--select", action="append", help="'Label=option'")
    oa.set_defaults(fn=cmd_op_act)
    ocon = o.add_parser("console", help="minimal web operator console")
    ocon.add_argument("--port", type=int, default=8500)
    ocon.set_defaults(fn=cmd_op_console)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    try:
        return args.fn(args, settings) or 0
    except (ConfigError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3 if args.cmd in ("replay", "invoke") else 1
    except Exception as e:  # HandoffError etc.
        if type(e).__name__ == "HandoffError":
            print(f"error: {e}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
