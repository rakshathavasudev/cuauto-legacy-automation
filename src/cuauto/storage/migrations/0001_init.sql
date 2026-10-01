-- Conventions: snake_case, plural table names, TEXT primary keys with a type prefix
-- (run_…, cap_…, int_…), ISO-8601 UTC timestamps in *_at columns, JSON in *_json columns,
-- enums enforced with CHECK constraints, every FK indexed.

CREATE TABLE capabilities (
    id              TEXT PRIMARY KEY,                 -- '<app_id>.<name>'
    app_id          TEXT NOT NULL,
    name            TEXT NOT NULL,
    title           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (app_id, name)
);

CREATE TABLE runs (
    id                     TEXT PRIMARY KEY,
    kind                   TEXT NOT NULL CHECK (kind IN ('discovery', 'replay')),
    capability_id          TEXT REFERENCES capabilities (id),
    capability_version     INTEGER,
    tenant_id              TEXT NOT NULL,
    goal_redacted          TEXT,
    status                 TEXT NOT NULL CHECK (status IN ('running', 'paused', 'success',
                                                           'business_outcome', 'failed', 'rejected')),
    outcome_code           TEXT,
    evidence_dir           TEXT NOT NULL,
    started_at             TEXT NOT NULL,
    finished_at            TEXT
);
CREATE INDEX idx_runs_capability ON runs (capability_id, capability_version);
CREATE INDEX idx_runs_tenant_started ON runs (tenant_id, started_at);

CREATE TABLE capability_versions (
    id               TEXT PRIMARY KEY,
    capability_id    TEXT NOT NULL REFERENCES capabilities (id),
    version          INTEGER NOT NULL CHECK (version >= 1),
    status           TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'deprecated')),
    artifact_path    TEXT NOT NULL,
    content_sha256   TEXT NOT NULL,
    source_run_id    TEXT REFERENCES runs (id),
    approved_by      TEXT,
    approved_at      TEXT,
    created_at       TEXT NOT NULL,
    UNIQUE (capability_id, version)
);
CREATE INDEX idx_capability_versions_capability ON capability_versions (capability_id);

CREATE TABLE run_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL REFERENCES runs (id),
    seq           INTEGER NOT NULL,
    ts            TEXT NOT NULL,
    type          TEXT NOT NULL,
    step_id       TEXT,
    payload_json  TEXT NOT NULL,                      -- always redacted before insert
    UNIQUE (run_id, seq)
);

CREATE TABLE interventions (
    id                TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES runs (id),
    kind              TEXT NOT NULL CHECK (kind IN ('stuck', 'approval')),
    status            TEXT NOT NULL CHECK (status IN ('open', 'claimed', 'resolved', 'expired')),
    reason            TEXT NOT NULL,
    step_id           TEXT,
    context_json      TEXT NOT NULL,
    screenshot_path   TEXT,
    session_endpoint  TEXT,                           -- CDP endpoint of the *live* session
    claimed_by        TEXT,
    resolution        TEXT CHECK (resolution IN ('resume', 'abort', 'approve', 'deny', 'timeout')),
    resolution_note   TEXT,
    created_at        TEXT NOT NULL,
    claimed_at        TEXT,
    resolved_at       TEXT
);
CREATE INDEX idx_interventions_status ON interventions (status, created_at);
CREATE INDEX idx_interventions_run ON interventions (run_id);

-- Single source of truth for "who is driving this session". `epoch` is a fencing token:
-- it increments on every transfer, and the automation checks it before every action.
CREATE TABLE control_leases (
    run_id        TEXT PRIMARY KEY REFERENCES runs (id),
    holder_kind   TEXT NOT NULL CHECK (holder_kind IN ('automation', 'human', 'none')),
    holder        TEXT NOT NULL,
    epoch         INTEGER NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE human_actions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    intervention_id   TEXT NOT NULL REFERENCES interventions (id),
    run_id            TEXT NOT NULL REFERENCES runs (id),
    ts                TEXT NOT NULL,
    action            TEXT NOT NULL,
    target_json       TEXT NOT NULL,
    value_redacted    TEXT,
    frame_url         TEXT
);
CREATE INDEX idx_human_actions_intervention ON human_actions (intervention_id);
