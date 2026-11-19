import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_SRC = os.path.join(REPO, "scripts", "update_webkit.py")
VENDOR_REAL = os.path.join(REPO, "projdeck", "vendor", "webkit")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    return subprocess.run(["git", "-C", str(cwd)] + list(args), check=True, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()


def make_source(path, files=None):
    os.makedirs(path)
    files = files or {"webkit.py": "print(1)\n", "base.css": "body{}\n", "poll.js": "1;\n"}
    for n, c in files.items():
        with open(os.path.join(path, n), "w") as f:
            f.write(c)
    with open(os.path.join(path, "MANIFEST"), "w") as f:
        f.write("\n".join(files) + "\n")
    git(path, "init", "-q", "-b", "main")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def proj(tmp_path):
    """A scratch copy of the script and vendor dir, so the real ones stay untouched."""
    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT_SRC, root / "scripts" / "update_webkit.py")
    shutil.copytree(VENDOR_REAL, root / "projdeck" / "vendor" / "webkit")
    return root


def run(proj, *args):
    return subprocess.run([sys.executable, str(proj / "scripts" / "update_webkit.py")] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def snapshot(proj):
    d = proj / "projdeck" / "vendor" / "webkit"
    return {n: (d / n).read_bytes() for n in sorted(os.listdir(str(d))) if (d / n).is_file()}


def test_vendored_files_match_source_json():
    with open(os.path.join(VENDOR_REAL, "SOURCE.json")) as f:
        rec = json.load(f)
    assert set(rec) == {"repo", "commit", "commit_date", "updated_at", "files"}
    assert rec["files"]
    for name, want in rec["files"].items():
        assert sha(os.path.join(VENDOR_REAL, name)) == want, name


def test_update_copies_and_records_commit(proj, tmp_path):
    src = tmp_path / "wk"
    head = make_source(str(src))
    r = run(proj, "--from", str(src))
    assert r.returncode == 0, r.stderr
    vendor = proj / "projdeck" / "vendor" / "webkit"
    for n in ("webkit.py", "base.css"):
        assert (vendor / n).read_bytes() == (src / n).read_bytes()
    rec = json.loads((vendor / "SOURCE.json").read_text())
    assert rec["commit"] == head and len(head) == 40
    assert rec["files"] == {"webkit.py": sha(str(src / "webkit.py")), "base.css": sha(str(src / "base.css")),
                          "poll.js": sha(str(src / "poll.js"))}
    assert rec["commit_date"] and rec["updated_at"]
    assert head[:7] in r.stdout and "webkit.py" in r.stdout
    assert run(proj, "--check", "--from", str(src)).returncode == 0
    assert run(proj, "--check").returncode == 0


def test_dirty_worktree_refused_and_nothing_changes(proj, tmp_path):
    src = tmp_path / "wk"
    make_source(str(src))
    (src / "webkit.py").write_text("print(2)\n")
    before = snapshot(proj)
    r = run(proj, "--from", str(src))
    assert r.returncode == 2
    assert snapshot(proj) == before


def test_untracked_file_counts_as_dirty(proj, tmp_path):
    src = tmp_path / "wk"
    make_source(str(src))
    (src / "extra.txt").write_text("x")
    before = snapshot(proj)
    assert run(proj, "--from", str(src)).returncode == 2
    assert snapshot(proj) == before


def test_not_a_git_repo_refused(proj, tmp_path):
    src = tmp_path / "plain"
    src.mkdir(exist_ok=True)
    (src / "MANIFEST").write_text("a.py\n")
    (src / "webkit.py").write_text("x")
    before = snapshot(proj)
    assert run(proj, "--from", str(src)).returncode == 2
    assert snapshot(proj) == before


def test_check_detects_hand_edit_and_changes_nothing(proj):
    assert run(proj, "--check").returncode == 0
    target = proj / "projdeck" / "vendor" / "webkit" / "poll.js"
    target.write_text(target.read_text() + "\n// tweak\n")
    before = snapshot(proj)
    r = run(proj, "--check")
    assert r.returncode == 1
    assert "poll.js" in r.stdout
    assert snapshot(proj) == before


def test_check_with_from_detects_copy_differing_from_source(proj, tmp_path):
    src = tmp_path / "wk"
    make_source(str(src))
    assert run(proj, "--from", str(src)).returncode == 0
    vendor = proj / "projdeck" / "vendor" / "webkit"
    # Hand edit that also rewrites SOURCE.json so the hash check alone would pass.
    (vendor / "webkit.py").write_text("print('evil')\n")
    rec = json.loads((vendor / "SOURCE.json").read_text())
    rec["files"]["webkit.py"] = sha(str(vendor / "webkit.py"))
    (vendor / "SOURCE.json").write_text(json.dumps(rec))
    assert run(proj, "--check").returncode == 0
    assert run(proj, "--check", "--from", str(src)).returncode == 1


def test_check_flags_missing_file(proj):
    os.remove(str(proj / "projdeck" / "vendor" / "webkit" / "base.css"))
    assert run(proj, "--check").returncode == 1


def _edit_source(proj, fn):
    path = proj / "projdeck" / "vendor" / "webkit" / "SOURCE.json"
    rec = json.loads(path.read_text())
    fn(rec)
    path.write_text(json.dumps(rec))


def test_check_fails_when_files_empty_or_missing(proj):
    _edit_source(proj, lambda r: r.__setitem__("files", {}))
    assert run(proj, "--check").returncode == 1
    _edit_source(proj, lambda r: r.pop("files"))
    assert run(proj, "--check").returncode == 1


def test_check_fails_when_edit_hides_by_dropping_entry(proj):
    vendor = proj / "projdeck" / "vendor" / "webkit"
    (vendor / "poll.js").write_text("// edited\n")
    _edit_source(proj, lambda r: r["files"].pop("poll.js"))
    r = run(proj, "--check")
    assert r.returncode == 1 and "poll.js" in r.stdout


def test_check_fails_on_unrecorded_extra_file(proj, tmp_path):
    (proj / "projdeck" / "vendor" / "webkit" / "extra.js").write_text("x")
    r = run(proj, "--check")
    assert r.returncode == 1 and "extra.js" in r.stdout


def test_check_ignores_init_and_pycache(proj):
    vendor = proj / "projdeck" / "vendor" / "webkit"
    (vendor / "__pycache__").mkdir(exist_ok=True)
    (vendor / "__pycache__" / "x.pyc").write_bytes(b"x")
    assert run(proj, "--check").returncode == 0
