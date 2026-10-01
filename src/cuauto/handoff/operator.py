"""Operator-side control transfer (used by the CLI and the console)."""
from __future__ import annotations

from ..storage.repo import Repo

RESOLUTIONS = {"stuck": {"resume", "abort"}, "approval": {"approve", "deny"}}


class HandoffError(Exception):
    pass


def claim(repo: Repo, iid: str, operator: str) -> int:
    iv = repo.get_intervention(iid)
    if not iv or iv["status"] != "open":
        raise HandoffError(f"intervention {iid} is not open")
    lease = repo.get_lease(iv["run_id"])
    if lease["holder_kind"] != "none":
        raise HandoffError(f"session is held by {lease['holder']}")
    epoch = repo.transfer_lease(iv["run_id"], expected_epoch=lease["epoch"], holder_kind="human",
                                holder=f"human:{operator}")
    if epoch is None or not repo.claim_intervention(iid, operator):
        raise HandoffError("lost the race: someone else claimed it")
    return epoch


def release(repo: Repo, iid: str, operator: str, resolution: str, note: str | None = None) -> int:
    iv = repo.get_intervention(iid)
    if not iv or iv["status"] not in ("open", "claimed"):
        raise HandoffError(f"intervention {iid} is not active")
    if resolution not in RESOLUTIONS[iv["kind"]]:
        raise HandoffError(f"'{resolution}' is not valid for a {iv['kind']} intervention; "
                           f"use one of {sorted(RESOLUTIONS[iv['kind']])}")
    lease = repo.get_lease(iv["run_id"])
    if iv["status"] == "claimed" and lease["holder"] != f"human:{operator}":
        raise HandoffError(f"only {lease['holder']} can hand control back")
    if iv["status"] == "open" and lease["holder_kind"] != "none":
        raise HandoffError("lease is not free")
    if iv["status"] == "open":
        repo.claim_intervention(iid, operator)  # decision without touching the session
    # Hand the session back first, then resolve: the engine reads the lease after it sees 'resolved'.
    epoch = repo.transfer_lease(iv["run_id"], expected_epoch=lease["epoch"], holder_kind="automation",
                                holder="automation")
    if epoch is None:
        raise HandoffError("lease changed concurrently; retry")
    repo.resolve_intervention(iid, resolution, note)
    return epoch


def require_control(repo: Repo, iid: str, operator: str) -> dict:
    iv = repo.get_intervention(iid)
    lease = repo.get_lease(iv["run_id"]) if iv else None
    if not lease or lease["holder"] != f"human:{operator}":
        raise HandoffError(f"{operator} does not hold the session; claim it first")
    return iv
