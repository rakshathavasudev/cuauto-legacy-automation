"""Artifact store: YAML files in git (reviewable in PRs) + a registry row per version in the DB.

Layout: capabilities/<app_id>/<name>/v<version>.yaml  — immutable once written.
"""
from __future__ import annotations

from pathlib import Path

from ..schema.artifact import Capability
from .repo import Repo


class ArtifactStore:
    def __init__(self, root: Path, repo: Repo):
        self.root = root
        self.repo = repo

    def save_new_version(self, cap: Capability, source_run_id: str | None) -> Capability:
        self.repo.upsert_capability(cap.id, cap.app.app_id, cap.name, cap.title)
        cap = cap.model_copy(update={"version": self.repo.next_version(cap.id)})
        path = self.root / cap.app.app_id / cap.name / f"v{cap.version}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"{path} already exists; artifacts are immutable")
        header = (f"# {cap.title} — v{cap.version}\n# Generated from run {source_run_id}. Content is immutable;"
                  f" approval state lives in the registry.\n")
        path.write_text(header + cap.to_yaml())
        self.repo.add_version(cap.id, cap.version, str(path.relative_to(self.root.parent)), cap.content_hash(),
                              source_run_id)
        return cap

    def load(self, cap_id: str, version: int | None = None) -> tuple[Capability, dict]:
        row = self.repo.get_version(cap_id, version)
        if row is None:
            raise KeyError(f"no such capability {cap_id} v{version or 'latest'}")
        cap = Capability.from_yaml((self.root.parent / row["artifact_path"]).read_text())
        if cap.content_hash() != row["content_sha256"]:
            raise ValueError(f"{cap_id} v{cap.version}: artifact content does not match registry hash (tampered?)")
        return cap, row
