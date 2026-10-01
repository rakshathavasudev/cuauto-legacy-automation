"""Write a one-table index of every run in an evidence directory (markdown on stdout)."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "evidence")
print("# Evidence index\n\n| run dir | kind | args / goal | status | code | recoveries | interventions |")
print("|---|---|---|---|---|---|---|")
for d in sorted((root / "runs").glob("*")):
    ev = [json.loads(line) for line in (d / "events.jsonl").read_text().splitlines() if line.strip()]
    start = ev[0] if ev else {}
    res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else {}
    kind = "discovery" if "_discovery_" in d.name else "replay"
    what = start.get("goal") if kind == "discovery" else json.dumps(start.get("args", {}))
    code = (res.get("outcome") or {}).get("code") or (res.get("failure") or {}).get("code") or ""
    recov = ", ".join(r["condition"] for r in res.get("recoveries", []))
    ivs = ", ".join(f"{i['kind']}:{i['resolution']} by {i['operator']}" for i in res.get("interventions", []))
    print(f"| `{d.name}` | {kind} | {(what or '')[:70]} | {res.get('status', '?')} | {code} | {recov} | {ivs} |")
