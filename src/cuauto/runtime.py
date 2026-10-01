"""Wiring: resolve tenant -> app profile -> policy, open the DB, build a surface."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .safety.policy import Policy
from .safety.redaction import Redactor
from .schema.profile import AppProfile, TenantConfig
from .settings import Settings
from .storage.artifacts import ArtifactStore
from .storage.db import connect, migrate
from .storage.repo import Repo
from .surface.web import WebSurface
from .util import new_id, utcnow


class ConfigError(Exception):
    pass


@dataclass
class Environment:
    settings: Settings
    tenant: TenantConfig
    profile: AppProfile
    policy: Policy
    aliases: dict[str, list[str]]
    secrets: dict[str, str]
    repo: Repo
    store: ArtifactStore

    def url(self, path: str) -> str:
        return self.tenant.base_url.rstrip("/") + path

    def redactor(self) -> Redactor:
        r = Redactor(self.profile.sensitive_labels)
        for v in self.secrets.values():
            r.register_secret(v)
        return r

    def new_surface(self, *, headed: bool = False) -> WebSurface:
        return WebSurface(policy=self.policy, headless=not headed, cdp_port=self.settings.cdp_port,
                          sensitive_labels=self.profile.sensitive_labels)

    def new_evidence_dir(self, kind: str, label: str) -> tuple[str, Path]:
        run_id = new_id("run")
        stamp = utcnow().replace(":", "").replace("-", "").split(".")[0]
        d = self.settings.evidence_dir / "runs" / f"{stamp}_{kind}_{label}_{run_id[-6:]}"
        d.mkdir(parents=True, exist_ok=True)
        return run_id, d


def open_repo(settings: Settings) -> Repo:
    conn = connect(settings.db_path)
    migrate(conn)
    return Repo(conn)


def load_environment(settings: Settings, tenant_id: str, *, need_secrets: bool = True) -> Environment:
    tpath = settings.config_dir / "tenants" / f"{tenant_id}.yaml"
    if not tpath.exists():
        raise ConfigError(f"unknown tenant '{tenant_id}' (no {tpath})")
    tenant = TenantConfig.load(tpath)
    profile = AppProfile.load(settings.config_dir / "apps" / tenant.app_id / "profile.yaml")
    parts = urlsplit(tenant.base_url)
    policy = Policy.load(settings.config_dir / "policy.yaml", extra_origins=[f"{parts.scheme}://{parts.netloc}"],
                         overrides=tenant.policy, risk_overrides=profile.risk_overrides)
    aliases: dict[str, list[str]] = {}
    for src in (profile.label_aliases, tenant.label_aliases):
        for k, v in src.items():
            aliases.setdefault(k, []).extend(v)
    secrets = {}
    if need_secrets:
        for key, env in (("secrets.username", tenant.credentials.username_env),
                         ("secrets.password", tenant.credentials.password_env)):
            val = os.environ.get(env)
            if not val:
                raise ConfigError(f"missing credential env var {env} for tenant {tenant_id} (see .env.example)")
            secrets[key] = val
    repo = open_repo(settings)
    return Environment(settings=settings, tenant=tenant, profile=profile, policy=policy, aliases=aliases,
                       secrets=secrets, repo=repo, store=ArtifactStore(settings.capabilities_dir, repo))
