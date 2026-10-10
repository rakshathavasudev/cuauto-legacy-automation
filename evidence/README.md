# Evidence

`scripts/demo.sh` regenerates everything in this folder. It produces:

- `SUMMARY.md` — a one-line-per-run index (status, outcome or failure code, recoveries, interventions).
- `artifact_member_savings_balance_vN.yaml` — the artifact produced by the real discovery run.
  The current evidence is for **v4**. `capabilities/` also holds v1–v3: earlier real discovery
  runs with the same model (`claude-sonnet-5-5`), kept because artifact versions are immutable.
  Their run evidence is in git history.
- `discovery_result.json` and `demo_console.log` — the full console transcript of the demo.
- `runs/<timestamp>_<kind>_<capability>_<id>/` — one directory per run, containing:
  - `events.jsonl` — every decision, action, checkpoint, recovery and handoff event, redacted.
    The same events are mirrored in the `run_events` table.
  - `result.json` — the result returned to the caller. Sensitive outputs appear as
    `[REDACTED:<sensitivity>]`; only the live caller receives real values.
  - `screens/*.png` — screenshots with money and PII blurred (final state, failures, escalations).
  - `snapshots/*.json` — the redacted semantic view at a failure or escalation. This is what the
    engine saw, not raw DOM.
  - `transcript.jsonl` — discovery runs only: model turns (reasoning, tool, input), redacted. `reasoning` is the
    model's thinking summary when it thinks, otherwise the `why` it gives with the action,
    with images omitted.

The runs to look at are:

| run | what it shows |
|---|---|
| discovery | a real LLM run with token usage and the model named in `transcript.jsonl` and the artifact's `provenance` |
| replay `NOT_APPROVED` | draft artifacts are not replayable unattended |
| `success` / `RECORD_NOT_FOUND` / `ACCESS_DENIED` / `INVALID_ARGUMENTS` | the outcome taxonomy |
| `system_notice`, `session_expired`, slow | recoveries, listed in `result.json.recoveries` |
| `APP_ERROR` | a hard failure with step, expected, observed, screenshot and snapshot |
| escalation | `intervention_opened` → `human_action` × N → `intervention_closed` → `resync` |
| `lakeshore-cu` | the same artifact on a second tenant; locators show `semantic+alias` |

`_offline_selftest/` holds the same flow driven by the offline scripted test double
(`model: scripted-offline`). It shows the machinery works without network access. It is not the
required real discovery run. Its screenshots are omitted to keep the repo small; the real
run's screenshots live under `runs/`.

Note: `demo_console.log` is the caller-side transcript. It shows command-line arguments and returned
outputs as the calling agent sees them. All data is synthetic mock data.
