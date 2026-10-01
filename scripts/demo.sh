#!/usr/bin/env bash
# End-to-end demo that produces everything in /evidence:
#   real LLM discovery -> artifact -> approval -> deterministic replays incl. errors,
#   recoveries, human escalation on the live session, and a second tenant variant.
#
#   OFFLINE=1 scripts/demo.sh   runs the same flow with the scripted test double and writes
#                               to evidence/_offline_selftest (never presented as real discovery).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
if [[ "${OFFLINE:-0}" == "1" ]]; then
  export CUAUTO_EVIDENCE_DIR="$PWD/evidence/_offline_selftest" CUAUTO_DB="$PWD/var/offline.db"
  export CUAUTO_CAPABILITIES_DIR="$PWD/var/offline_capabilities"
  LLM="scripted:tests/fixtures/plan_member_savings_balance.json"; FORCE="--force"
else
  : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY (or put it in .env) — or run OFFLINE=1}" 2>/dev/null || \
    grep -q '^ANTHROPIC_API_KEY=.\+' .env || { echo "ANTHROPIC_API_KEY missing (env or .env)"; exit 1; }
  LLM="anthropic"; FORCE=""
fi
EV="${CUAUTO_EVIDENCE_DIR:-$PWD/evidence}"
mkdir -p "$EV" var
LOG="$EV/demo_console.log"; : > "$LOG"
say() { printf '\n\033[1m== %s\033[0m\n' "$*" | tee -a "$LOG"; }
run() { echo "\$ cuauto $*" >> "$LOG"; set +e; cuauto "$@" 2>>"$LOG" | tee -a "$LOG"; local rc=${PIPESTATUS[0]}; set -e; echo "(exit $rc)" | tee -a "$LOG"; }

scripts/mocks.sh start
cuauto db init >/dev/null
cuauto faults --tenant buffalo-teachers --clear >/dev/null

say "1. Discovery (LLM: $LLM) on tenant buffalo-teachers"
cuauto discover --tenant buffalo-teachers --goal-file goals/member_savings_balance.yaml --llm "$LLM" \
  2>>"$LOG" | tee "$EV/discovery_result.json" | tee -a "$LOG"
VER=$(python -c "import json;print(json.load(open('$EV/discovery_result.json'))['capability']['version'])")
CAP=legacy-corebank.member_savings_balance

say "2. Review + approve v$VER (draft -> approved)"
run capabilities show $CAP --version "$VER"
run replay $CAP --version "$VER" --tenant buffalo-teachers --arg member_id=10001   # NOT_APPROVED (rejected)
run capabilities approve $CAP --version "$VER" --by rakshatha $FORCE

say "3. Replays: success / business outcomes / contract rejection"
run replay $CAP --tenant buffalo-teachers --arg member_id=10001
run replay $CAP --tenant buffalo-teachers --arg member_id=7777777
run replay $CAP --tenant buffalo-teachers --arg member_id=55555
run replay $CAP --tenant buffalo-teachers --arg member_id=12ab

say "4. Recoverable runtime conditions"
cuauto faults --tenant buffalo-teachers --set interstitial_once@/cgi/mbrdtl >/dev/null
run replay $CAP --tenant buffalo-teachers --arg member_id=10002
cuauto faults --tenant buffalo-teachers --set session_expire_once@/cgi/mbrsrch >/dev/null
run replay $CAP --tenant buffalo-teachers --arg member_id=10001
cuauto faults --tenant buffalo-teachers --clear >/dev/null
cuauto faults --tenant buffalo-teachers --set slow@/cgi/mbrdtl >/dev/null
run replay $CAP --tenant buffalo-teachers --arg member_id=10001
cuauto faults --tenant buffalo-teachers --clear >/dev/null

say "5. Hard failure (application ABEND) with screenshot + snapshot"
cuauto faults --tenant buffalo-teachers --set error@/cgi/mbrdtl >/dev/null
run replay $CAP --tenant buffalo-teachers --arg member_id=10001
cuauto faults --tenant buffalo-teachers --clear >/dev/null

say "6. Unknown state -> human takes over the SAME live session -> hands back"
cuauto faults --tenant buffalo-teachers --set override_once@/cgi/mbrdtl >/dev/null
python scripts/operator_bot.py --as alice --fill "Override Code=2468" --click Authorize 2>>"$LOG" &
run replay $CAP --tenant buffalo-teachers --arg member_id=10001 --on-stuck escalate --escalation-timeout 120
wait || true
cuauto faults --tenant buffalo-teachers --clear >/dev/null
run operator list --all

say "7. Same artifact, second tenant (vendor variant b, v4.3.0) via label aliases"
run replay $CAP --tenant lakeshore-cu --arg member_id=10001

say "8. Agent-facing catalog + invoke by tool name"
run catalog
run invoke legacy-corebank__member_savings_balance --tenant buffalo-teachers --args '{"member_id": "10002"}'

ART=$(ls "${CUAUTO_CAPABILITIES_DIR:-capabilities}"/legacy-corebank/member_savings_balance/v"$VER".yaml)
cp "$ART" "$EV/artifact_member_savings_balance_v$VER.yaml"
python scripts/summarize_evidence.py "$EV" > "$EV/SUMMARY.md"
say "Done. See $EV/SUMMARY.md"
