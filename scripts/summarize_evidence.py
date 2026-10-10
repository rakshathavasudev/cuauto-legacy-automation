"""Write a one-table index of every run in an evidence directory (markdown on stdout)."""
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "evidence")

# demo_console.log interleaves "$ cuauto <cmd> ..." lines with each command's JSON result; map run_id -> command
commands: dict[str, str] = {}
no_run: list[str] = []
log = root / "demo_console.log"
if log.exists():
    cmd = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("$ cuauto "):
            if cmd and cmd not in commands.values():
                no_run.append(cmd)
            cmd = line[len("$ cuauto "):]
            continue
        m = re.search(r'"run_id": "(run_[0-9a-f]+)"', line)
        if m and cmd and m.group(1) not in commands:
            commands[m.group(1)] = cmd
    if cmd and cmd not in commands.values():
        no_run.append(cmd)


def verb(c: str) -> str:
    """Short label: the subcommand plus the flags that change behaviour."""
    parts = c.split()
    flags = [p for p in parts if p in ("--on-stuck", "--version")]
    extra = " ".join(f"{f} {parts[parts.index(f) + 1]}" for f in flags if parts.index(f) + 1 < len(parts))
    return f"`{parts[0]}{' ' + extra if extra else ''}`"


print("# Evidence index\n\n| run dir | command | tenant | args / goal | status | code | locators | recoveries | interventions |")
print("|---|---|---|---|---|---|---|---|---|")
for d in sorted((root / "runs").glob("*")):
    if not (d / "events.jsonl").exists():
        continue
    ev = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines() if line.strip()]
    start = ev[0] if ev else {}
    res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else {}
    kind = "discovery" if "_discovery_" in d.name else "replay"
    what = start.get("goal") if kind == "discovery" else json.dumps(start.get("args", {}))
    tenant = res.get("tenant_id") or start.get("tenant") or next((e["tenant"] for e in ev if e.get("tenant")), "")
    run_id = res.get("run_id") or start.get("run_id")
    command = verb(commands[run_id]) if run_id in commands else f"`{kind}`"
    code = (res.get("outcome") or {}).get("code") or (res.get("failure") or {}).get("code") or ""
    strategies = {loc["strategy"] for loc in res.get("locators", [])}
    locators = ", ".join(sorted(strategies - {"semantic"})) or ("semantic" if strategies else "")
    if any(loc.get("drift") for loc in res.get("locators", [])):
        locators += " (drift)"
    recov = ", ".join(r["condition"] for r in res.get("recoveries", []))
    ivs = ", ".join(f"{i['kind']}:{i['resolution']} by {i['operator']}" for i in res.get("interventions", []))
    print(f"| `{d.name}` | {command} | {tenant} | {(what or '')[:70]} | {res.get('status', '?')} | {code} "
          f"| {locators} | {recov} | {ivs} |")

if no_run:
    print("\nCommands that create no run directory (their output is in `demo_console.log`):\n")
    for c in no_run:
        print(f"- `cuauto {c}`")
