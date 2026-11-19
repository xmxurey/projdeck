"""F13: `proj clean` (list / trash) needs git status only; a failing git log must not stop it."""
import os

import pytest

from projdeck import agent


@pytest.fixture
def log_fails(monkeypatch):
    real = agent.git

    def git(path, *args):
        if args and args[0] == "log":
            raise RuntimeError("fatal: log is broken")
        return real(path, *args)
    monkeypatch.setattr(agent, "git", git)


def repo_with_junk(tmp_path, make_repo):
    repo = make_repo(tmp_path / "r")
    with open(os.path.join(repo, "x.log"), "w") as f:
        f.write("junk")
    return repo


def test_list_works_when_git_log_fails(tmp_path, make_repo, log_fails):
    repo = repo_with_junk(tmp_path, make_repo)
    out = agent.act_list({"path": repo})
    assert [f["path"] for f in out["files"]] == ["x.log"]


def test_trash_works_when_git_log_fails(tmp_path, make_repo, log_fails):
    repo = repo_with_junk(tmp_path, make_repo)
    res = agent.act_trash({"path": repo, "files": ["x.log"], "trash_dir": str(tmp_path / "t"),
                           "now": 1_800_000_000})
    assert res["moved"] == [os.path.join(repo, "x.log")] and res["skipped"] == []


def test_collect_still_reports_the_log_failure(tmp_path, make_repo, log_fails):
    repo = repo_with_junk(tmp_path, make_repo)
    with pytest.raises(RuntimeError, match="log is broken"):
        agent.git_info(repo)
    info, untracked = agent.git_status(repo)
    assert untracked == ["x.log"] and info["branch"] == "main"
