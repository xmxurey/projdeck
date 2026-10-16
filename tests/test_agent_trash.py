import json
import os

import pytest

from projdeck import agent
from tests.conftest import run_git, write


@pytest.fixture
def repo(tmp_path, make_repo):
    r = make_repo(tmp_path / "proj", {".gitignore": ".env\n", "keep.py": "k"})
    write(os.path.join(r, ".env"), "SECRET")
    write(os.path.join(r, "debug.log"), "log")
    write(os.path.join(r, "notes", "idea.txt"), "idea")
    return r


def test_list_only_untracked_not_ignored(repo):
    out = agent.ACTIONS["list"]({"path": repo})
    assert sorted((f["path"], f["suspicious"]) for f in out["files"]) == [
        ("debug.log", True), ("notes/idea.txt", False)]


def test_trash_restore_roundtrip(repo, tmp_path):
    trash = str(tmp_path / "trash")
    res = agent.ACTIONS["trash"]({"path": repo, "files": ["debug.log", "notes/idea.txt"],
                                  "trash_dir": trash, "now": 1700000000})
    assert res["skipped"] == [] and len(res["moved"]) == 2
    assert not os.path.exists(os.path.join(repo, "debug.log"))
    batches = agent.ACTIONS["batches"]({"trash_dir": trash})["batches"]
    assert [(b["id"], b["count"], b["project"]) for b in batches] == [(res["batch"], 2, repo)]
    back = agent.ACTIONS["restore"]({"batch": res["batch"], "trash_dir": trash})
    assert len(back["restored"]) == 2 and back["skipped"] == []
    with open(os.path.join(repo, "notes", "idea.txt")) as f:
        assert f.read() == "idea"
    assert agent.ACTIONS["batches"]({"trash_dir": trash})["batches"] == []


def test_trash_refuses_tracked_ignored_and_escaping_paths(repo, tmp_path):
    trash = str(tmp_path / "trash")
    res = agent.ACTIONS["trash"]({"path": repo, "trash_dir": trash,
                                  "files": ["keep.py", ".env", "../outside", "/etc/passwd"]})
    assert res["batch"] is None and res["moved"] == []
    assert sorted(res["skipped"]) == sorted(["keep.py", ".env", "../outside", "/etc/passwd"])
    assert os.path.exists(os.path.join(repo, ".env"))


def test_trash_skips_file_tracked_after_listing(repo, tmp_path):
    run_git(repo, "add", "debug.log")
    res = agent.ACTIONS["trash"]({"path": repo, "files": ["debug.log"],
                                  "trash_dir": str(tmp_path / "t")})
    assert res["skipped"] == ["debug.log"]


def test_restore_does_not_overwrite(repo, tmp_path):
    trash = str(tmp_path / "trash")
    res = agent.ACTIONS["trash"]({"path": repo, "files": ["debug.log"], "trash_dir": trash})
    write(os.path.join(repo, "debug.log"), "new")
    back = agent.ACTIONS["restore"]({"batch": res["batch"], "trash_dir": trash})
    assert back["restored"] == [] and back["skipped"] == [os.path.join(repo, "debug.log")]
    assert len(agent.ACTIONS["batches"]({"trash_dir": trash})["batches"]) == 1


def test_restore_rejects_bad_batch_id(tmp_path):
    with pytest.raises(ValueError):
        agent.ACTIONS["restore"]({"batch": "../x", "trash_dir": str(tmp_path)})


def test_purge_only_old_batches(repo, tmp_path):
    trash = str(tmp_path / "trash")
    old = agent.ACTIONS["trash"]({"path": repo, "files": ["debug.log"], "trash_dir": trash,
                                  "now": 1000})
    new = agent.ACTIONS["trash"]({"path": repo, "files": ["notes/idea.txt"],
                                  "trash_dir": trash, "now": 10 * 86400})
    out = agent.ACTIONS["purge"]({"older_days": 5, "now": 10 * 86400 + 1, "trash_dir": trash})
    assert out["purged"] == [old["batch"]]
    assert [b["id"] for b in agent.ACTIONS["batches"]({"trash_dir": trash})["batches"]] == [new["batch"]]
