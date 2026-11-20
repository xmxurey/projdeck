"""T03 spec tests: non-UTF-8 paths survive list -> trash -> restore byte for byte, and only the
selected files move. Everything lives under tmp_path; trash dir too."""
import os
import subprocess

from projdeck import agent
from tests.conftest import GIT_ENV, run_git

BAD = b"junk\xff.log"
BAD2 = b"junk\xfe.log"


def s(b):
    return b.decode("utf-8", "surrogateescape")


def put(repo, rel, content=b"x"):
    p = os.path.join(os.fsencode(repo), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(content)
    return p


def listing(repo):
    return os.listdir(os.fsencode(repo))


def act(name, req):
    return agent.ACTIONS[name](req)


def test_roundtrip_non_utf8_name_byte_exact(tmp_path, make_repo):
    repo = make_repo(tmp_path / "p")
    put(repo, BAD, b"\x00\xffpayload\n")
    files = act("list", {"path": repo})["files"]
    assert [f["path"] for f in files] == [s(BAD)]
    trash = str(tmp_path / "trash")
    res = act("trash", {"path": repo, "files": [files[0]["path"]], "trash_dir": trash, "now": 1700000000})
    assert res["skipped"] == [] and len(res["moved"]) == 1
    assert BAD not in listing(repo)
    back = act("restore", {"batch": res["batch"], "trash_dir": trash})
    assert len(back["restored"]) == 1 and back["skipped"] == []
    assert BAD in listing(repo)
    with open(os.path.join(os.fsencode(repo), BAD), "rb") as f:
        assert f.read() == b"\x00\xffpayload\n"
    assert act("batches", {"trash_dir": trash})["batches"] == []


def test_non_utf8_directory_component_roundtrip(tmp_path, make_repo):
    repo = make_repo(tmp_path / "p")
    put(repo, b"d\xffir/inner.txt", b"in")
    files = act("list", {"path": repo})["files"]
    assert [f["path"] for f in files] == [s(b"d\xffir/inner.txt")]
    trash = str(tmp_path / "trash")
    res = act("trash", {"path": repo, "files": [files[0]["path"]], "trash_dir": trash})
    assert res["skipped"] == [] and len(res["moved"]) == 1
    act("restore", {"batch": res["batch"], "trash_dir": trash})
    with open(os.path.join(os.fsencode(repo), b"d\xffir", b"inner.txt"), "rb") as f:
        assert f.read() == b"in"


def test_only_selected_file_moves(tmp_path, make_repo):
    repo = make_repo(tmp_path / "p")
    put(repo, BAD, b"bad")
    put(repo, BAD2, b"bad2")
    put(repo, b"plain.log", b"plain")
    put(repo, "café.log".encode(), b"utf8")
    trash = str(tmp_path / "trash")
    res = act("trash", {"path": repo, "files": [s(BAD)], "trash_dir": trash})
    assert res["skipped"] == [] and len(res["moved"]) == 1
    left = set(listing(repo))
    assert BAD not in left
    assert {BAD2, b"plain.log", "café.log".encode()} <= left
    for name, content in ((BAD2, b"bad2"), (b"plain.log", b"plain")):
        with open(os.path.join(os.fsencode(repo), name), "rb") as f:
            assert f.read() == content
    back = act("restore", {"batch": res["batch"], "trash_dir": trash})
    assert len(back["restored"]) == 1
    assert BAD in set(listing(repo))


def test_selecting_ascii_leaves_non_utf8_alone(tmp_path, make_repo):
    repo = make_repo(tmp_path / "p")
    put(repo, BAD, b"bad")
    put(repo, b"plain.log", b"plain")
    res = act("trash", {"path": repo, "files": ["plain.log"], "trash_dir": str(tmp_path / "t")})
    assert len(res["moved"]) == 1
    assert BAD in listing(repo) and b"plain.log" not in listing(repo)


def test_tracked_dotdot_and_missing_are_skipped(tmp_path, make_repo):
    repo = make_repo(tmp_path / "p", {"README.md": "hi"})
    put(repo, b"tracked\xff.txt", b"t")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "more")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    put(repo, BAD, b"untracked")  # exists, but not selected
    sel = ["README.md", s(b"tracked\xff.txt"), "../outside.txt", s(b"nothere\xff.txt")]
    trash = str(tmp_path / "trash")
    res = act("trash", {"path": repo, "files": sel, "trash_dir": trash})
    assert res["batch"] is None and res["moved"] == []
    assert sorted(res["skipped"]) == sorted(sel)
    assert outside.read_text() == "keep"
    assert {b"README.md", b"tracked\xff.txt", BAD} <= set(listing(repo))
    assert not os.path.exists(trash) or os.listdir(trash) == [] or act("batches", {"trash_dir": trash})["batches"] == []


def test_non_utf8_file_tracked_after_listing_is_skipped(tmp_path, make_repo):
    repo = make_repo(tmp_path / "p")
    put(repo, BAD, b"x")
    name = act("list", {"path": repo})["files"][0]["path"]
    run_git(repo, "add", "-A")
    res = act("trash", {"path": repo, "files": [name], "trash_dir": str(tmp_path / "t")})
    assert res["skipped"] == [name] and res["moved"] == []
    assert BAD in listing(repo)


def test_restore_does_not_overwrite_non_utf8(tmp_path, make_repo):
    repo = make_repo(tmp_path / "p")
    put(repo, BAD, b"old")
    trash = str(tmp_path / "trash")
    res = act("trash", {"path": repo, "files": [s(BAD)], "trash_dir": trash})
    put(repo, BAD, b"new")
    back = act("restore", {"batch": res["batch"], "trash_dir": trash})
    assert back["restored"] == [] and len(back["skipped"]) == 1
    with open(os.path.join(os.fsencode(repo), BAD), "rb") as f:
        assert f.read() == b"new"


def test_collect_with_invalid_utf8_branch_and_commit_message(tmp_path, make_repo):
    repo = make_repo(tmp_path / "root" / "p", {"a.py": "1"})
    env = dict(os.environ, **GIT_ENV)
    rb = os.fsencode(repo)
    subprocess.run([b"git", b"-C", rb, b"checkout", b"-q", b"-b", b"br\xffanch"], check=True, env=env,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    put(repo, b"a.py", b"2")
    subprocess.run([b"git", b"-C", rb, b"commit", b"-q", b"-a", b"-m", b"bad \xff msg"], check=True,
                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    put(repo, BAD, b"u")
    out = agent.collect({"host": "h1", "roots": [str(tmp_path / "root")], "scan_depth": 2,
                         "claude_dir": str(tmp_path / "claude")})
    p = {x["path"]: x for x in out["projects"]}[repo]
    assert p["errors"] == []
    assert p["git"]["untracked"] == 1


def test_git_isolation_still_effective(tmp_path, make_repo):
    repo = make_repo(tmp_path / "p", {"a.py": "1"})
    marker = tmp_path / "ran"
    hook = tmp_path / "hook.sh"
    hook.write_text("#!/bin/sh\necho x >> %s\n" % marker)
    hook.chmod(0o755)
    run_git(repo, "config", "core.fsmonitor", str(hook))
    run_git(repo, "config", "core.pager", str(hook))
    put(repo, BAD, b"u")
    act("list", {"path": repo})
    assert not marker.exists()


def test_list_does_not_rewrite_index(tmp_path, make_repo):
    repo = make_repo(tmp_path / "p", {"a.py": "1"})
    idx = os.path.join(repo, ".git", "index")
    os.utime(os.path.join(repo, "a.py"), (1_000_000_000, 1_000_000_000))  # stat change, same content
    with open(idx, "rb") as f:
        before = f.read()
    put(repo, BAD, b"u")
    act("list", {"path": repo})
    with open(idx, "rb") as f:
        assert f.read() == before
