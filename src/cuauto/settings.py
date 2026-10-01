"""Process-wide settings, resolved from environment (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    home: Path
    db_path: Path
    capabilities_dir: Path
    evidence_dir: Path
    config_dir: Path
    model: str
    anthropic_api_key: str | None
    cdp_port: int
    operator_console_url: str


def load_settings() -> Settings:
    home = Path(os.environ.get("CUAUTO_HOME", Path.cwd())).resolve()
    _load_dotenv(home / ".env")
    return Settings(
        home=home,
        db_path=Path(os.environ.get("CUAUTO_DB", home / "var" / "cuauto.db")),
        capabilities_dir=Path(os.environ.get("CUAUTO_CAPABILITIES_DIR", home / "capabilities")),
        evidence_dir=Path(os.environ.get("CUAUTO_EVIDENCE_DIR", home / "evidence")),
        config_dir=home / "config",
        model=os.environ.get("CUAUTO_MODEL", "claude-sonnet-5-5"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        cdp_port=int(os.environ.get("CUAUTO_CDP_PORT", "9222")),
        operator_console_url=os.environ.get("CUAUTO_OPERATOR_URL", "http://127.0.0.1:8500"),
    )
