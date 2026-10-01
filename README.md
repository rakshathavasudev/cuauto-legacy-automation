# cuauto — computer-use discovery → reviewable capabilities → deterministic replay

An LLM explores a hostile legacy back-office UI once to accomplish a goal. The system records what
actually happened as a typed, versioned **capability artifact**. That artifact is then replayed
deterministically, with no model in the loop, as a tool an AI agent can call. Unknown states
escalate to a human, who takes control of the *same live session* and hands it back. The human's
actions are recorded.

The design is described in [REPORT.md](REPORT.md). Run evidence is in [evidence/](evidence/).

## Repository layout

```
src/cuauto/
  schema/        artifact (capability) · app profile / tenant · run result   — pydantic v2, versioned
  surface/       perception + acting: base.py (adapter seam), web.py (Playwright), perception_js.py
  agent/         discovery loop (loop.py), model clients (llm.py), prompts, recorder (trace -> artifact)
  replay/        deterministic engine (engine.py) + execution primitives/detectors (executor.py)
  handoff/       control lease + escalation (controller.py), operator ops, live-session acting, console
  safety/        allowlist / risk policy, redaction
  storage/       SQLite + numbered migrations, repository (all SQL), immutable artifact store
  evidence.py    structured, redacted run logs
  runtime.py     tenant -> app profile -> policy wiring
  cli.py         `cuauto` entry point
mock_bank/       "Legacy CoreBank" — framesets, table layout, no ids, opaque names, runtime faults
config/          policy.yaml · apps/<app>/profile.yaml (vendor product) · tenants/<tenant>.yaml
goals/           discovery goals (inputs are declared, typed, sensitivity-tagged)
capabilities/    artifacts: capabilities/<app_id>/<name>/v<N>.yaml (immutable, committed to git)
evidence/        artifacts + logs of real runs (see evidence/README.md)
scripts/         mocks.sh · demo.sh · operator_bot.py · summarize_evidence.py
tests/           unit tests + browser end-to-end tests
```

## Setup

You need Python 3.11+ and Chromium (installed through Playwright).

```bash
git clone https://github.com/rakshathavasudev/cuauto-legacy-automation.git && cd cuauto-legacy-automation
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
cp .env.example .env          # then set ANTHROPIC_API_KEY (only needed for discovery)
```

`.env` keys:

| key | purpose |
|---|---|
| `ANTHROPIC_API_KEY` | discovery only (replay never calls a model) |
| `CUAUTO_MODEL` | discovery model, default `claude-sonnet-5-5` |
| `BTFCU_USERNAME` / `BTFCU_PASSWORD`, `LSCU_USERNAME` / `LSCU_PASSWORD` | operator credentials per tenant (mock defaults: `teller1` / `demo-pass-123`) |
| `CUAUTO_CDP_PORT` | port where the live browser session is exposed for operator takeover (default 9222) |
| `CUAUTO_DB`, `CUAUTO_EVIDENCE_DIR`, `CUAUTO_CAPABILITIES_DIR` | optional path overrides |

## Running without live services

Nothing external is required except the model API for discovery. The target application is local.

```bash
scripts/mocks.sh start        # tenant buffalo-teachers on :8401 (variant a), lakeshore-cu on :8402 (variant b)
cuauto db init
```

Without an API key, you can still run everything with the offline scripted test double. It
replays a fixed plan through the same loop and recorder. Its artifacts are stamped
`model: scripted-offline`, and `capabilities approve` refuses them unless you pass `--force`.

```bash
OFFLINE=1 scripts/demo.sh     # writes to evidence/_offline_selftest
pytest                        # unit + e2e (e2e starts the mocks itself if needed)
```

## Demo: discover → approve → replay

To produce everything in `/evidence` in one go, run `scripts/demo.sh`. The individual steps are:

```bash
# 1. Discovery: the model explores the live UI and a draft artifact is written
cuauto discover --tenant buffalo-teachers --goal-file goals/member_savings_balance.yaml
#    flags: --headed  --no-vision (semantic view only)  --max-steps N  --no-escalation

# 2. Review and approve (content is immutable; status lives in the registry)
cuauto capabilities list
cuauto capabilities show legacy-corebank.member_savings_balance --version 1
cuauto capabilities approve legacy-corebank.member_savings_balance --version 1 --by <you>

# 3. Deterministic replay (no LLM). Exit codes: 0 success, 2 business outcome, 1 failed, 3 rejected
cuauto replay legacy-corebank.member_savings_balance --tenant buffalo-teachers --arg member_id=10001
cuauto replay legacy-corebank.member_savings_balance --tenant buffalo-teachers --arg member_id=7777777  # RECORD_NOT_FOUND
cuauto replay legacy-corebank.member_savings_balance --tenant buffalo-teachers --arg member_id=55555    # ACCESS_DENIED
cuauto replay legacy-corebank.member_savings_balance --tenant buffalo-teachers --arg member_id=12ab     # rejected

# 4. Inject runtime conditions into the mock and replay again
cuauto faults --tenant buffalo-teachers --set interstitial_once@/cgi/mbrdtl     # recovered automatically
cuauto faults --tenant buffalo-teachers --set session_expire_once@/cgi/mbrsrch  # re-auth + restart
cuauto faults --tenant buffalo-teachers --set error@/cgi/mbrdtl                 # hard failure + evidence
cuauto faults --tenant buffalo-teachers --clear

# 5. Same artifact on a second tenant (different labels and version, via aliases)
cuauto replay legacy-corebank.member_savings_balance --tenant lakeshore-cu --arg member_id=10001

# 6. Agent-facing interface
cuauto catalog                                             # approved capabilities as tool definitions
cuauto invoke legacy-corebank__member_savings_balance --tenant buffalo-teachers --args '{"member_id":"10002"}'
```

A second goal, `goals/open_sub_account_review.yaml`, covers a multi-field form that ends on a
confirmation screen. Discovery stops before the irreversible "Confirm and Open" step by design.

## Human-in-the-loop handoff

Run with `--on-stuck escalate` (or let discovery hit a dead end). When the run hits a state it
cannot explain, it opens an intervention and cedes control of the live browser session. It then
waits for an operator:

```bash
cuauto faults --tenant buffalo-teachers --set override_once@/cgi/mbrdtl
cuauto replay legacy-corebank.member_savings_balance --tenant buffalo-teachers \
    --arg member_id=10001 --on-stuck escalate

# in another terminal (or the console: cuauto operator console -> http://127.0.0.1:8500)
cuauto operator list
cuauto operator claim <intervention_id> --as alice                 # lease -> human:alice
cuauto operator act   <intervention_id> --as alice --fill "Override Code=2468" --click Authorize
cuauto operator release <intervention_id> --as alice --resolution resume   # lease -> automation
```

The operator acts on the *same* browser (same cookies, frames and page state), attached over
CDP. Every human click, fill and select is captured by the running automation, redacted, and
stored in `human_actions`. After hand-back, the engine re-syncs to the furthest checkpoint the
screen satisfies; it does not assume the human finished the step. Irreversible steps open an
`approval` intervention, which is resolved with `--resolution approve|deny`.

## Database conventions

- **Engine:** SQLite in WAL mode, with forward-only numbered migrations in `storage/migrations/NNNN_*.sql`, tracked in `schema_migrations`.
- **Naming:** tables are snake_case plurals; ids are `TEXT` with a type prefix (`run_`, `int_`, `capv_`); every timestamp is `*_at`, ISO-8601 UTC.
- **Constraints:** enums are enforced with `CHECK`, and every foreign key is indexed.
- **Code:** all SQL lives in `storage/repo.py`. Control transfer is a compare-and-set on `control_leases.epoch`.
- **What is stored where:** artifacts are YAML files (reviewable in PRs). The DB stores their path and `content_sha256`, which is verified on every load.
