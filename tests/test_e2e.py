"""End-to-end: scripted discovery -> artifact -> deterministic replay under every runtime condition.

Uses the offline ScriptedClient so it runs in CI without an API key; the real LLM discovery
run lives in /evidence. Drives a real Chromium against the mock bank.
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CAP = "legacy-corebank.member_savings_balance"
pytestmark = pytest.mark.e2e


def cli(env, *args, timeout=150):
    p = subprocess.run([sys.executable, "-m", "cuauto.cli", *args], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=timeout)
    try:
        out = json.loads(p.stdout) if p.stdout.strip().startswith(("{", "[")) else p.stdout
    except json.JSONDecodeError:
        out = p.stdout
    return p.returncode, out, p.stderr


def fault(spec: str | None, port=8401):
    q = "clear=1" if spec is None else f"set={spec}"
    urllib.request.urlopen(f"http://127.0.0.1:{port}/_admin/faults?{q}", timeout=3)


@pytest.fixture(scope="module")
def recorded(mocks, home):
    tmp, env = home
    assert cli(env, "db", "init")[0] == 0
    code, out, err = cli(env, "discover", "--tenant", "buffalo-teachers", "--goal-file",
                         "goals/member_savings_balance.yaml", "--llm",
                         "scripted:tests/fixtures/plan_member_savings_balance.json", "--no-escalation")
    assert code == 0, err
    assert out["capability"]["id"] == CAP
    return env


def replay(env, member, *extra, tenant="buffalo-teachers"):
    return cli(env, "replay", CAP, "--tenant", tenant, "--arg", f"member_id={member}", *extra)


def test_artifact_has_no_recorded_values(recorded, home):
    text = next((home[0] / "capabilities").rglob("v1.yaml")).read_text()
    assert "10001" not in text and "1,234" not in text and "{{member_id}}" in text


def test_draft_is_not_replayable_unattended(recorded):
    code, out, _ = replay(recorded, "10001")
    assert code == 3 and out["failure"]["code"] == "NOT_APPROVED"
    assert cli(recorded, "capabilities", "approve", CAP, "--version", "1", "--by", "test", "--force")[0] == 0


@pytest.mark.parametrize("member,code,status,key", [
    ("10001", 0, "success", None),
    ("10002", 0, "success", None),
    ("7777777", 2, "business_outcome", "RECORD_NOT_FOUND"),
    ("55555", 2, "business_outcome", "ACCESS_DENIED"),
    ("12ab", 3, "rejected", "INVALID_ARGUMENTS"),
])
def test_outcome_taxonomy(recorded, member, code, status, key):
    fault(None)
    rc, out, err = replay(recorded, member)
    assert (rc, out["status"]) == (code, status), err
    if status == "success":
        assert out["outputs"]["savings_balance"] in ("1234.56", "25.00")
    elif status == "business_outcome":
        assert out["outcome"]["code"] == key
    else:
        assert out["failure"]["code"] == key


@pytest.mark.parametrize("spec,recovery", [
    ("interstitial_once@/cgi/mbrdtl", "system_notice"),
    ("session_expire_once@/cgi/mbrsrch", "session_expired"),
])
def test_recoverable_conditions(recorded, spec, recovery):
    fault(None)
    fault(spec)
    rc, out, err = replay(recorded, "10001")
    fault(None)
    assert rc == 0, err
    assert recovery in [r["condition"] for r in out["recoveries"]]


def test_hard_failure_is_debuggable_and_redacted(recorded, home):
    fault("error@/cgi/mbrdtl")
    rc, out, _ = replay(recorded, "10001")
    fault(None)
    assert rc == 1
    f = out["failure"]
    assert f["code"] == "APP_ERROR" and f["step_id"] == "s4" and f["observed"] and f["evidence"]
    persisted = json.dumps(out["failure"])
    assert "10001" not in persisted


def test_escalation_human_takes_live_session_and_hands_back(recorded):
    fault(None)
    fault("override_once@/cgi/mbrdtl")
    bot = subprocess.Popen([sys.executable, "scripts/operator_bot.py", "--as", "alice", "--fill",
                            "Override Code=2468", "--click", "Authorize"], cwd=ROOT, env=recorded,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    rc, out, err = replay(recorded, "10001", "--on-stuck", "escalate", "--escalation-timeout", "90")
    bot.wait(timeout=30)
    fault(None)
    assert rc == 0, err
    iv = out["interventions"][0]
    assert iv["operator"] == "alice" and iv["resolution"] == "resume" and iv["human_actions"] >= 2
    assert out["completed_with_human"] is True
    # the override code the human typed never reaches the logs
    assert "2468" not in err


def test_cross_tenant_variant_via_aliases(recorded):
    rc, out, err = replay(recorded, "10001", tenant="lakeshore-cu")
    assert rc == 0, err
    assert any("+alias" in loc["strategy"] for loc in out["locators"])


def test_catalog_and_invoke(recorded):
    rc, tools, _ = cli(recorded, "catalog")
    assert rc == 0 and tools[0]["name"] == "legacy-corebank__member_savings_balance"
    rc, out, _ = cli(recorded, "invoke", tools[0]["name"], "--tenant", "buffalo-teachers",
                     "--args", '{"member_id": "10002"}')
    assert rc == 0 and out["outputs"]["savings_balance"] == "25.00"


def test_irreversible_step_is_gated(mocks, recorded, home):
    """A capability whose last step commits: blocked without confirmation, human-approved otherwise."""
    os.environ.update(recorded)
    from cuauto.runtime import load_environment
    from cuauto.schema.artifact import Expectation, Step, SuccessCondition, Target, TargetContext
    from cuauto.settings import load_settings
    env = load_environment(load_settings(), "buffalo-teachers")
    code, _, err = cli(recorded, "discover", "--tenant", "buffalo-teachers", "--goal-file",
                       "goals/open_sub_account_review.yaml", "--llm",
                       "scripted:tests/fixtures/plan_open_sub_account_review.json", "--no-escalation")
    assert code == 0, err
    review, _ = env.store.load("legacy-corebank.open_sub_account_review")
    commit = Step(id="s10", intent="Commit the new sub-account", action="click", risk="irreversible",
                  target=Target(role="button", name="Confirm and Open", context=TargetContext(frame="main")),
                  expect=Expectation(frame="main", text_present=["SUB-ACCOUNT OPENED"]))
    cap = review.model_copy(update={
        "name": "open_sub_account", "id": "legacy-corebank.open_sub_account", "title": "Open a sub-account",
        "side_effects": "irreversible", "steps": review.steps + [commit],
        "success": SuccessCondition(all_of=[Expectation(frame="main", text_present=["SUB-ACCOUNT OPENED"])],
                                    require_outputs=False)})
    env.store.save_new_version(type(cap).model_validate(cap.to_dict()), None)
    args = ["replay", "legacy-corebank.open_sub_account", "--tenant", "buffalo-teachers", "--allow-draft",
            "--arg", "member_id=10001", "--arg", "share_type=Savings Club", "--arg", "nickname=Rainy",
            "--arg", "opening_deposit=10.00"]
    rc, out, _ = cli(recorded, *args)
    assert rc == 1 and out["failure"]["code"] == "APPROVAL_REQUIRED"
    # a draft cannot be self-confirmed by the caller either
    rc, out, _ = cli(recorded, *args, "--confirm-irreversible")
    assert out["failure"]["code"] == "APPROVAL_REQUIRED"
    bot = subprocess.Popen([sys.executable, "scripts/operator_bot.py", "--as", "carol", "--resolution", "approve"],
                           cwd=ROOT, env=recorded, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    rc, out, err = cli(recorded, *args, "--on-stuck", "escalate", "--escalation-timeout", "60")
    bot.wait(timeout=30)
    assert rc == 0, err
    assert out["interventions"][0]["kind"] == "approval" and out["interventions"][0]["resolution"] == "approve"
