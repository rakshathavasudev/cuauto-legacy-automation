import pytest

from cuauto.handoff.operator import HandoffError, claim, release
from cuauto.storage.db import connect, migrate
from cuauto.storage.repo import Repo


@pytest.fixture
def repo(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    r = Repo(conn)
    r.create_run(kind="replay", tenant_id="t", evidence_dir="e", run_id="run_1")
    r.init_lease("run_1")
    return r


def _escalate(repo):
    iid = repo.create_intervention(run_id="run_1", kind="stuck", reason="x", step_id="s1", context={},
                                   screenshot_path=None, session_endpoint="http://127.0.0.1:9222")
    assert repo.transfer_lease("run_1", expected_epoch=1, holder_kind="none", holder="awaiting_operator") == 2
    return iid


def test_claim_is_exclusive_and_release_returns_control(repo):
    iid = _escalate(repo)
    assert claim(repo, iid, "alice") == 3
    with pytest.raises(HandoffError):
        claim(repo, iid, "bob")
    with pytest.raises(HandoffError, match="only human:alice"):
        release(repo, iid, "bob", "resume")
    with pytest.raises(HandoffError, match="not valid"):
        release(repo, iid, "alice", "approve")
    assert release(repo, iid, "alice", "resume") == 4
    lease = repo.get_lease("run_1")
    assert (lease["holder_kind"], lease["epoch"]) == ("automation", 4)
    assert repo.get_intervention(iid)["resolution"] == "resume"


def test_stale_epoch_cannot_transfer(repo):
    _escalate(repo)
    assert repo.transfer_lease("run_1", expected_epoch=1, holder_kind="automation", holder="automation") is None


def test_approval_without_taking_the_session(repo):
    iid = repo.create_intervention(run_id="run_1", kind="approval", reason="x", step_id="s1", context={},
                                   screenshot_path=None, session_endpoint=None)
    repo.transfer_lease("run_1", expected_epoch=1, holder_kind="none", holder="awaiting_operator")
    release(repo, iid, "carol", "approve")
    assert repo.get_intervention(iid)["claimed_by"] == "carol"
