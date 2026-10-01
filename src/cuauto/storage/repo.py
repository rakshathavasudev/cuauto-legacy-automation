"""Repository: all SQL lives here. Callers get plain dicts."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..util import new_id, utcnow


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


class Repo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- capabilities -----------------------------------------------------------------------
    def upsert_capability(self, cap_id: str, app_id: str, name: str, title: str) -> None:
        now = utcnow()
        self.conn.execute(
            "INSERT INTO capabilities (id, app_id, name, title, created_at, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at",
            (cap_id, app_id, name, title, now, now))

    def next_version(self, cap_id: str) -> int:
        r = self.conn.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM capability_versions WHERE capability_id=?",
                              (cap_id,)).fetchone()
        return int(r["v"])

    def add_version(self, cap_id: str, version: int, path: str, sha: str, source_run_id: str | None) -> str:
        vid = new_id("capv")
        self.conn.execute(
            "INSERT INTO capability_versions (id, capability_id, version, status, artifact_path, content_sha256, "
            "source_run_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (vid, cap_id, version, "draft", path, sha, source_run_id, utcnow()))
        return vid

    def get_version(self, cap_id: str, version: int | None = None) -> dict | None:
        if version is None:
            return _row(self.conn.execute(
                "SELECT * FROM capability_versions WHERE capability_id=? AND status!='deprecated' "
                "ORDER BY (status='approved') DESC, version DESC LIMIT 1", (cap_id,)).fetchone())
        return _row(self.conn.execute("SELECT * FROM capability_versions WHERE capability_id=? AND version=?",
                                      (cap_id, version)).fetchone())

    def set_version_status(self, cap_id: str, version: int, status: str, by: str | None) -> bool:
        cur = self.conn.execute(
            "UPDATE capability_versions SET status=?, approved_by=?, approved_at=? WHERE capability_id=? AND version=?",
            (status, by, utcnow() if status == "approved" else None, cap_id, version))
        return cur.rowcount == 1

    def list_capabilities(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT c.id, c.title, v.version, v.status, v.content_sha256, v.artifact_path, v.created_at "
            "FROM capabilities c JOIN capability_versions v ON v.capability_id=c.id ORDER BY c.id, v.version")]

    # -- runs ---------------------------------------------------------------------------------
    def create_run(self, *, kind: str, tenant_id: str, evidence_dir: str, goal: str | None = None,
                   capability_id: str | None = None, capability_version: int | None = None,
                   run_id: str | None = None) -> str:
        rid = run_id or new_id("run")
        self.conn.execute(
            "INSERT INTO runs (id, kind, capability_id, capability_version, tenant_id, goal_redacted, status, "
            "evidence_dir, started_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, kind, capability_id, capability_version, tenant_id, goal, "running", evidence_dir, utcnow()))
        return rid

    def finish_run(self, run_id: str, status: str, outcome_code: str | None) -> None:
        self.conn.execute("UPDATE runs SET status=?, outcome_code=?, finished_at=? WHERE id=?",
                          (status, outcome_code, utcnow(), run_id))

    def set_run_status(self, run_id: str, status: str) -> None:
        self.conn.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))

    def get_run(self, run_id: str) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def add_event(self, run_id: str, seq: int, ts: str, type_: str, step_id: str | None, payload: dict) -> None:
        self.conn.execute("INSERT INTO run_events (run_id, seq, ts, type, step_id, payload_json) VALUES (?,?,?,?,?,?)",
                          (run_id, seq, ts, type_, step_id, json.dumps(payload, default=str)))

    # -- control leases (compare-and-set on epoch) --------------------------------------------
    def init_lease(self, run_id: str) -> int:
        self.conn.execute("INSERT INTO control_leases (run_id, holder_kind, holder, epoch, updated_at) "
                          "VALUES (?, 'automation', 'automation', 1, ?)", (run_id, utcnow()))
        return 1

    def get_lease(self, run_id: str) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM control_leases WHERE run_id=?", (run_id,)).fetchone())

    def transfer_lease(self, run_id: str, *, expected_epoch: int, holder_kind: str, holder: str) -> int | None:
        """Atomically move control; returns the new epoch, or None if someone else moved it first."""
        cur = self.conn.execute(
            "UPDATE control_leases SET holder_kind=?, holder=?, epoch=epoch+1, updated_at=? "
            "WHERE run_id=? AND epoch=?", (holder_kind, holder, utcnow(), run_id, expected_epoch))
        return expected_epoch + 1 if cur.rowcount == 1 else None

    # -- interventions --------------------------------------------------------------------------
    def create_intervention(self, *, run_id: str, kind: str, reason: str, step_id: str | None, context: dict,
                            screenshot_path: str | None, session_endpoint: str | None) -> str:
        iid = new_id("int")
        self.conn.execute(
            "INSERT INTO interventions (id, run_id, kind, status, reason, step_id, context_json, screenshot_path, "
            "session_endpoint, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (iid, run_id, kind, "open", reason, step_id, json.dumps(context, default=str), screenshot_path,
             session_endpoint, utcnow()))
        return iid

    def get_intervention(self, iid: str) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM interventions WHERE id=?", (iid,)).fetchone())

    def list_interventions(self, status: tuple[str, ...] = ("open", "claimed")) -> list[dict]:
        q = f"SELECT * FROM interventions WHERE status IN ({','.join('?' * len(status))}) ORDER BY created_at"
        return [dict(r) for r in self.conn.execute(q, status)]

    def claim_intervention(self, iid: str, operator: str) -> bool:
        cur = self.conn.execute("UPDATE interventions SET status='claimed', claimed_by=?, claimed_at=? "
                                "WHERE id=? AND status='open'", (operator, utcnow(), iid))
        return cur.rowcount == 1

    def resolve_intervention(self, iid: str, resolution: str, note: str | None, status: str = "resolved") -> bool:
        cur = self.conn.execute(
            "UPDATE interventions SET status=?, resolution=?, resolution_note=?, resolved_at=? "
            "WHERE id=? AND status IN ('open','claimed')", (status, resolution, note, utcnow(), iid))
        return cur.rowcount == 1

    def add_human_action(self, *, intervention_id: str, run_id: str, action: str, target: dict,
                         value_redacted: str | None, frame_url: str | None) -> None:
        self.conn.execute(
            "INSERT INTO human_actions (intervention_id, run_id, ts, action, target_json, value_redacted, frame_url) "
            "VALUES (?,?,?,?,?,?,?)",
            (intervention_id, run_id, utcnow(), action, json.dumps(target), value_redacted, frame_url))

    def human_actions(self, intervention_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM human_actions WHERE intervention_id=? ORDER BY id",
                                                    (intervention_id,))]
