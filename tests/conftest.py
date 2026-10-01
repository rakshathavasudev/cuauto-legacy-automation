import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _up(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/_admin/faults?clear=1", timeout=1)
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def mocks():
    procs = []
    for port, variant in ((8401, "a"), (8402, "b")):
        if not _up(port):
            procs.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "mock_bank.app:app", "--port", str(port), "--log-level", "warning"],
                cwd=ROOT, env={**os.environ, "MOCK_VARIANT": variant}))
    for port in (8401, 8402):
        for _ in range(50):
            if _up(port):
                break
            time.sleep(0.2)
    yield
    for p in procs:
        p.terminate()


@pytest.fixture(scope="session")
def home(tmp_path_factory):
    """Isolated DB / capabilities / evidence; config comes from the repo."""
    tmp = tmp_path_factory.mktemp("cuauto")
    env = {**os.environ, "CUAUTO_HOME": str(ROOT), "CUAUTO_DB": str(tmp / "var" / "cuauto.db"),
           "CUAUTO_CAPABILITIES_DIR": str(tmp / "capabilities"), "CUAUTO_EVIDENCE_DIR": str(tmp / "evidence"),
           "CUAUTO_CDP_PORT": "9333", "BTFCU_USERNAME": "teller1", "BTFCU_PASSWORD": "demo-pass-123",
           "LSCU_USERNAME": "teller1", "LSCU_PASSWORD": "demo-pass-123"}
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    if not env["PLAYWRIGHT_BROWSERS_PATH"]:
        env.pop("PLAYWRIGHT_BROWSERS_PATH")
    return tmp, env
