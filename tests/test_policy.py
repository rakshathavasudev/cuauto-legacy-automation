from pathlib import Path

from cuauto.safety.policy import Policy

ROOT = Path(__file__).resolve().parents[1]


def policy() -> Policy:
    return Policy.load(ROOT / "config/policy.yaml", extra_origins=["http://127.0.0.1:8401"],
                       risk_overrides={"Open Sub-Account": "safe", "Confirm and Open": "irreversible"})


def test_url_allowlist_default_deny():
    p = policy()
    assert p.check_url("http://127.0.0.1:8401/cgi/mbrinq").allowed
    assert not p.check_url("http://127.0.0.1:8401/_admin/faults?set=error").allowed
    assert not p.check_url("http://127.0.0.1:8401/logout").allowed
    assert not p.check_url("http://evil.example/cgi/x").allowed
    assert not p.check_url("http://127.0.0.1:8401/secret-report").allowed


def test_action_allowlist():
    p = policy()
    assert p.check_action("click").allowed
    assert not p.check_action("drag").allowed


def test_risk_classification():
    p = policy()
    assert p.classify("click", "button", "Confirm and Open") == "irreversible"
    assert p.classify("click", "button", "Post Transfer") == "irreversible"
    assert p.classify("click", "button", "Open Sub-Account") == "safe"  # override beats pattern-free default
    assert p.classify("click", "link", "View") == "safe"
    assert p.classify("fill", "textbox", "Nickname") == "reversible"
