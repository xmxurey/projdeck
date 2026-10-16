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


def test_restore_keeps_unlisted_files_in_batch(repo, tmp_path):
    trash = str(tmp_path / "trash")
    res = agent.ACTIONS["trash"]({"path": repo, "files": ["debug.log"], "trash_dir": trash})
    batch_dir = os.path.join(trash, res["batch"])
    stray = os.path.join(batch_dir, "stray.txt")
    write(stray, "not in manifest")
    back = agent.ACTIONS["restore"]({"batch": res["batch"], "trash_dir": trash})
    assert back["restored"] == [os.path.join(repo, "debug.log")]
    assert os.path.exists(os.path.join(repo, "debug.log"))
    assert os.path.exists(stray)
    assert not os.path.exists(os.path.join(batch_dir, "manifest.json"))


def test_restore_partial_failure_updates_manifest(repo, tmp_path, monkeypatch):
    trash = str(tmp_path / "trash")
    res = agent.ACTIONS["trash"]({"path": repo, "files": ["debug.log", "notes/idea.txt"],
                                  "trash_dir": trash})
    real_move = agent.shutil.move
    calls = []

    def flaky_move(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError("disk went away")
        return real_move(src, dst)

    monkeypatch.setattr(agent.shutil, "move", flaky_move)
    with pytest.raises(OSError):
        agent.ACTIONS["restore"]({"batch": res["batch"], "trash_dir": trash})
    monkeypatch.undo()
    batches = agent.ACTIONS["batches"]({"trash_dir": trash})["batches"]
    assert [(b["id"], b["count"]) for b in batches] == [(res["batch"], 1)]
    assert os.path.exists(os.path.join(repo, "debug.log"))
    assert not os.path.exists(os.path.join(repo, "notes", "idea.txt"))


def test_list_and_trash_refuse_subdirectory_of_repo(tmp_path, make_repo):
    g = make_repo(tmp_path / "g", {"sub/sub/new.log": "tracked"})
    write(os.path.join(g, "sub", "new.log"), "untracked")
    trash = str(tmp_path / "trash")
    with pytest.raises(ValueError, match="repository root"):
        agent.ACTIONS["list"]({"path": os.path.join(g, "sub")})
    with pytest.raises(ValueError, match="repository root"):
        agent.ACTIONS["trash"]({"path": os.path.join(g, "sub"), "files": ["sub/new.log"],
                                "trash_dir": trash})
    assert os.path.exists(os.path.join(g, "sub", "sub", "new.log"))
    assert os.path.exists(os.path.join(g, "sub", "new.log"))
    assert not os.path.exists(trash)


def test_list_and_trash_refuse_non_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    for act in ("list", "trash"):
        with pytest.raises(ValueError, match="not a git repository"):
            agent.ACTIONS[act]({"path": str(d), "files": [], "trash_dir": str(tmp_path / "t")})


def _make_batch(trash, name, created, project="/p"):
    os.makedirs(os.path.join(trash, name))
    with open(os.path.join(trash, name, "manifest.json"), "w") as f:
        f.write('{"created": %d, "project": "%s", "items": []}' % (created, project))


def test_purge_with_ids_deletes_only_listed(tmp_path):
    trash = str(tmp_path / "trash")
    _make_batch(trash, "a", 1)
    _make_batch(trash, "b", 1)
    res = agent.ACTIONS["purge"]({"trash_dir": trash, "ids": ["a", "nope"], "older_days": 0})
    assert res["purged"] == ["a"]
    assert os.listdir(trash) == ["b"]
    for bad in ("", ".", "..", "x/y"):
        with pytest.raises(ValueError):
            agent.ACTIONS["purge"]({"trash_dir": trash, "ids": [bad]})
    assert os.listdir(trash) == ["b"]


def test_purge_rejects_negative_older_days(tmp_path):
    with pytest.raises(ValueError):
        agent.ACTIONS["purge"]({"trash_dir": str(tmp_path), "older_days": -1})


def test_load_batches_skips_bad_manifest(tmp_path):
    trash = str(tmp_path / "trash")
    _make_batch(trash, "good", 5)
    os.makedirs(os.path.join(trash, "bad"))
    with open(os.path.join(trash, "bad", "manifest.json"), "w") as f:
        f.write('{"created": 1}')
    os.makedirs(os.path.join(trash, "weird"))
    with open(os.path.join(trash, "weird", "manifest.json"), "w") as f:
        f.write("[1]")
    assert [b["id"] for b in agent.ACTIONS["batches"]({"trash_dir": trash})["batches"]] == ["good"]
