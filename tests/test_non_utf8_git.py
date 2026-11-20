import json
import os
import subprocess

import pytest

from projdeck import agent
from tests.conftest import GIT_ENV, run_git, write

BAD = b"junk\xff.log"


@pytest.fixture
def repo(tmp_path, make_repo):
    r = make_repo(tmp_path / "proj", {"keep.py": "k"})
    with open(os.path.join(os.fsencode(r), BAD), "wb") as f:
        f.write(b"\x00\xfebinary")
    write(os.path.join(r, "plain.log"), "plain")
    return r


def _bad_rel():
    return os.fsdecode(BAD)


def test_list_returns_non_utf8_name_losslessly(repo):
    out = agent.ACTIONS["list"]({"path": repo})
    assert sorted(f["path"] for f in out["files"]) == sorted([_bad_rel(), "plain.log"])
    json.dumps(out)  # the agent's own output path must stay serialisable


def test_trash_restore_roundtrip_non_utf8(repo, tmp_path):
    trash = str(tmp_path / "trash")
    res = agent.ACTIONS["trash"]({"path": repo, "files": [_bad_rel()], "trash_dir": trash,
                                  "now": 1700000000})
    assert res["skipped"] == [] and len(res["moved"]) == 1
    # only the selected file moved
    assert not os.path.lexists(os.path.join(os.fsencode(repo), BAD))
    assert os.path.exists(os.path.join(repo, "plain.log"))
    back = agent.ACTIONS["restore"]({"batch": res["batch"], "trash_dir": trash})
    assert back["skipped"] == [] and len(back["restored"]) == 1
    with open(os.path.join(os.fsencode(repo), BAD), "rb") as f:
        assert f.read() == b"\x00\xfebinary"
    assert os.listdir(os.fsencode(repo)).count(BAD) == 1


def test_roundtrip_through_json_main(repo, tmp_path, capsys):
    trash = str(tmp_path / "trash")
    req = {"action": "trash", "path": repo, "files": [_bad_rel()], "trash_dir": trash}
    assert agent.main(["agent", json.dumps(req)]) == 0
    res = json.loads(capsys.readouterr().out)
    assert len(res["moved"]) == 1
    req = {"action": "restore", "batch": res["batch"], "trash_dir": trash}
    assert agent.main(["agent", json.dumps(req)]) == 0
    assert os.path.exists(os.path.join(os.fsencode(repo), BAD))


def test_trash_skips_unselected_and_unsafe(repo, tmp_path):
    run_git(repo, "add", "keep.py")  # tracked already
    trash = str(tmp_path / "trash")
    missing = "gone\udcff.txt"
    files = ["keep.py", "../outside", missing]
    res = agent.ACTIONS["trash"]({"path": repo, "files": files, "trash_dir": trash})
    assert res["batch"] is None and res["moved"] == []
    assert sorted(res["skipped"]) == sorted(files)
    assert os.path.lexists(os.path.join(os.fsencode(repo), BAD))
    assert os.path.exists(os.path.join(repo, "plain.log"))
    assert not os.path.exists(trash)


def test_trash_selects_only_the_chosen_non_utf8_file(repo, tmp_path):
    res = agent.ACTIONS["trash"]({"path": repo, "files": [_bad_rel()],
                                  "trash_dir": str(tmp_path / "t")})
    assert len(res["moved"]) == 1 and res["skipped"] == []
    assert os.listdir(repo).count("plain.log") == 1


def test_collect_survives_invalid_utf8_branch_and_subject(tmp_path, make_repo, monkeypatch):
    home = tmp_path / "home"
    r = make_repo(home / "p", {"a": "1"})
    rb = os.fsencode(r)
    for argv in ([b"checkout", b"-q", b"-b", b"br\xffanch"],
                 [b"commit", b"-q", b"--allow-empty", b"-m", b"bad \xff msg"]):
        subprocess.run([b"git", b"-C", rb] + argv, check=True, env=dict(os.environ, **GIT_ENV),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    monkeypatch.setenv("HOME", str(home))
    out = agent.collect({"roots": [str(home)], "scan_depth": 2,
                         "claude_dir": str(tmp_path / "claude")})
    proj = {p["path"]: p for p in out["projects"]}[r]
    assert proj["errors"] == []
    assert proj["git"]["branch"] == "br\udcffanch"
    json.dumps(out)
