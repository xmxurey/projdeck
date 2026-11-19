import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest


GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}


def run_git(path, *args):
    subprocess.run(["git", "-C", str(path)] + list(args), check=True,
                   env=dict(os.environ, **GIT_ENV), stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def write(path, content="x"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w") as f:
        f.write(content)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_VENDOR = os.path.join(ROOT, "projdeck", "vendor", "webkit")
SCRIPT = os.path.join(ROOT, "scripts", "update_webkit.py")
NAMES = ["webkit.py", "base.css", "poll.js"]


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def snapshot(d):
    out = {}
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if os.path.isfile(p):
            out[n] = (sha(p), os.stat(p).st_mtime_ns)
    return out


def head(path):
    return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], check=True,
                          stdout=subprocess.PIPE).stdout.decode().strip()


@pytest.fixture
def sandbox(tmp_path, make_repo):
    """A private copy of the project layout (script + vendor dir) plus a fake webkit repo."""
    proj = tmp_path / "proj"
    (proj / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, str(proj / "scripts" / "update_webkit.py"))
    vendor = proj / "projdeck" / "vendor" / "webkit"
    shutil.copytree(REAL_VENDOR, str(vendor), ignore=shutil.ignore_patterns("__pycache__"))
    src = make_repo(tmp_path / "webkit", {
        "MANIFEST": "webkit.py\nbase.css\npoll.js\n",
        "webkit.py": "def h(s):\n    return s\n# new\n",
        "base.css": "body{color:red}\n",
        "poll.js": "console.log(1);\n",
        "README.md": "readme",
    })

    class S:
        pass
    s = S()
    s.proj, s.vendor, s.src = str(proj), str(vendor), src
    s.source_json = str(vendor / "SOURCE.json")

    def run(*args):
        return subprocess.run([sys.executable, str(proj / "scripts" / "update_webkit.py")] + list(args),
                              cwd=str(proj), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    s.run = run
    return s


def test_real_vendor_hashes_match_source_json():
    with open(os.path.join(REAL_VENDOR, "SOURCE.json")) as f:
        meta = json.load(f)
    assert set(meta) >= {"repo", "commit", "commit_date", "updated_at", "files"}
    assert set(meta["files"]) == set(NAMES)
    for name, digest in meta["files"].items():
        assert sha(os.path.join(REAL_VENDOR, name)) == digest


def test_real_seed_source_json():
    with open(os.path.join(REAL_VENDOR, "SOURCE.json")) as f:
        meta = json.load(f)
    # seed stage (before W1): repo "seed" with null commit; afterwards a real 40-char sha
    if meta["repo"] == "seed":
        assert meta["commit"] is None
    else:
        assert len(meta["commit"]) == 40


def test_update_copies_and_records_source(sandbox):
    r = sandbox.run("--from", sandbox.src)
    assert r.returncode == 0, r.stderr
    for n in NAMES:
        assert open(os.path.join(sandbox.vendor, n), "rb").read() == \
            open(os.path.join(sandbox.src, n), "rb").read()
    meta = json.load(open(sandbox.source_json))
    assert meta["commit"] == head(sandbox.src)
    assert len(meta["commit"]) == 40
    assert meta["files"] == {n: sha(os.path.join(sandbox.src, n)) for n in NAMES}
    assert meta["repo"] != "seed"
    assert meta["commit_date"] and meta["updated_at"]
    assert not os.path.exists(os.path.join(sandbox.vendor, "README.md"))


def test_update_only_copies_manifest_files(sandbox):
    write(os.path.join(sandbox.src, "MANIFEST"), "webkit.py\n")
    run_git(sandbox.src, "add", "-A")
    run_git(sandbox.src, "commit", "-q", "-m", "m")
    before_css = sha(os.path.join(sandbox.vendor, "base.css"))
    r = sandbox.run("--from", sandbox.src)
    assert r.returncode == 0, r.stderr
    assert sha(os.path.join(sandbox.vendor, "base.css")) == before_css
    assert open(os.path.join(sandbox.vendor, "webkit.py")).read().endswith("# new\n")


def test_update_idempotent_and_check_passes_after(sandbox):
    assert sandbox.run("--from", sandbox.src).returncode == 0
    assert sandbox.run("--from", sandbox.src).returncode == 0
    assert sandbox.run("--check").returncode == 0
    assert sandbox.run("--check", "--from", sandbox.src).returncode == 0


def test_update_output_mentions_new_commit(sandbox):
    r = sandbox.run("--from", sandbox.src)
    out = (r.stdout + r.stderr).decode()
    assert head(sandbox.src)[:7] in out


@pytest.mark.parametrize("dirty", ["untracked", "modified"])
def test_dirty_source_refused(sandbox, dirty):
    if dirty == "untracked":
        write(os.path.join(sandbox.src, "junk.txt"), "x")
    else:
        write(os.path.join(sandbox.src, "base.css"), "changed\n")
    snap, meta = snapshot(sandbox.vendor), open(sandbox.source_json).read()
    r = sandbox.run("--from", sandbox.src)
    assert r.returncode == 2
    assert snapshot(sandbox.vendor) == snap
    assert open(sandbox.source_json).read() == meta


def test_clean_source_accepted_counterpart(sandbox):
    # same repo, clean -> not refused
    assert sandbox.run("--from", sandbox.src).returncode == 0


def test_non_git_source_refused(sandbox, tmp_path):
    plain = tmp_path / "plain"
    for n in NAMES:
        write(str(plain / n), "z")
    write(str(plain / "MANIFEST"), "\n".join(NAMES))
    snap, meta = snapshot(sandbox.vendor), open(sandbox.source_json).read()
    r = sandbox.run("--from", str(plain))
    assert r.returncode == 2
    assert snapshot(sandbox.vendor) == snap
    assert open(sandbox.source_json).read() == meta


def test_missing_source_dir_does_not_touch_copy(sandbox, tmp_path):
    snap = snapshot(sandbox.vendor)
    r = sandbox.run("--from", str(tmp_path / "nope"))
    assert r.returncode != 0
    assert snapshot(sandbox.vendor) == snap


def test_check_clean_exit_0_and_no_writes(sandbox):
    snap, meta = snapshot(sandbox.vendor), open(sandbox.source_json).read()
    r = sandbox.run("--check")
    assert r.returncode == 0, r.stderr
    assert snapshot(sandbox.vendor) == snap
    assert open(sandbox.source_json).read() == meta


@pytest.mark.parametrize("name", NAMES)
def test_check_detects_hand_edit(sandbox, name):
    with open(os.path.join(sandbox.vendor, name), "a") as f:
        f.write("\n// hand edit\n")
    snap, meta = snapshot(sandbox.vendor), open(sandbox.source_json).read()
    r = sandbox.run("--check")
    assert r.returncode == 1
    assert snapshot(sandbox.vendor) == snap
    assert open(sandbox.source_json).read() == meta


def test_check_after_real_update_detects_hand_edit(sandbox):
    assert sandbox.run("--from", sandbox.src).returncode == 0
    with open(os.path.join(sandbox.vendor, "poll.js"), "a") as f:
        f.write("x")
    assert sandbox.run("--check").returncode == 1


def test_check_with_from_ignores_later_source_commits(sandbox):
    # spec: compares the *recorded* commit's files, so newer commits in the source are fine
    assert sandbox.run("--from", sandbox.src).returncode == 0
    write(os.path.join(sandbox.src, "base.css"), "body{color:blue}\n")
    run_git(sandbox.src, "add", "-A")
    run_git(sandbox.src, "commit", "-q", "-m", "css")
    assert sandbox.run("--check", "--from", sandbox.src).returncode == 0


def test_check_with_from_detects_copy_differing_from_recorded_commit(sandbox):
    assert sandbox.run("--from", sandbox.src).returncode == 0
    # hand edit + forge SOURCE.json hash so the self-consistency check alone passes
    p = os.path.join(sandbox.vendor, "base.css")
    write(p, "forged\n")
    meta = json.load(open(sandbox.source_json))
    meta["files"]["base.css"] = sha(p)
    json.dump(meta, open(sandbox.source_json, "w"))
    assert sandbox.run("--check").returncode == 0
    snap = snapshot(sandbox.vendor)
    r = sandbox.run("--check", "--from", sandbox.src)
    assert r.returncode == 1
    assert snapshot(sandbox.vendor) == snap


def test_real_vendor_untouched_by_these_tests():
    with open(os.path.join(REAL_VENDOR, "SOURCE.json")) as f:
        meta = json.load(f)
    for name, digest in meta["files"].items():
        assert sha(os.path.join(REAL_VENDOR, name)) == digest


def _set_files(sandbox, mutate):
    meta = json.load(open(sandbox.source_json))
    mutate(meta)
    json.dump(meta, open(sandbox.source_json, "w"))


def test_check_fails_when_files_emptied(sandbox):
    _set_files(sandbox, lambda m: m.update(files={}))
    snap = snapshot(sandbox.vendor)
    assert sandbox.run("--check").returncode == 1
    assert snapshot(sandbox.vendor) == snap


def test_check_fails_when_files_key_missing(sandbox):
    _set_files(sandbox, lambda m: m.pop("files"))
    assert sandbox.run("--check").returncode == 1


def test_check_fails_when_entry_dropped_and_file_hand_edited(sandbox):
    _set_files(sandbox, lambda m: m["files"].pop("poll.js"))
    with open(os.path.join(sandbox.vendor, "poll.js"), "a") as f:
        f.write("// edit\n")
    assert sandbox.run("--check").returncode == 1


def test_check_fails_when_entry_dropped_even_unedited(sandbox):
    _set_files(sandbox, lambda m: m["files"].pop("poll.js"))
    assert sandbox.run("--check").returncode == 1


def test_check_fails_when_registered_file_missing(sandbox):
    os.remove(os.path.join(sandbox.vendor, "base.css"))
    assert sandbox.run("--check").returncode == 1


def test_check_fails_on_unregistered_stray_file(sandbox):
    write(os.path.join(sandbox.vendor, "extra.js"), "alert(1)\n")
    snap = snapshot(sandbox.vendor)
    r = sandbox.run("--check")
    assert r.returncode == 1
    assert "extra.js" in (r.stdout + r.stderr).decode()
    assert snapshot(sandbox.vendor) == snap


def test_check_ignores_init_and_pycache(sandbox):
    write(os.path.join(sandbox.vendor, "__init__.py"), "")
    write(os.path.join(sandbox.vendor, "__pycache__", "webkit.cpython-312.pyc"), "bin")
    assert sandbox.run("--check").returncode == 0


def test_update_keeps_old_file_then_check_flags_it(sandbox):
    write(os.path.join(sandbox.src, "MANIFEST"), "webkit.py\n")
    run_git(sandbox.src, "add", "-A")
    run_git(sandbox.src, "commit", "-q", "-m", "m")
    assert sandbox.run("--from", sandbox.src).returncode == 0
    assert os.path.exists(os.path.join(sandbox.vendor, "base.css"))
    assert sandbox.run("--check").returncode == 1
