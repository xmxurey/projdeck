import json
import os
import subprocess

import pytest

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}


def run_git(path, *args):
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", "-C", str(path)] + list(args), check=True, env=env,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def write(path, content="x"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w") as f:
        f.write(content)


@pytest.fixture
def make_repo():
    def _make(path, files=None, commit=True):
        path = str(path)
        os.makedirs(path, exist_ok=True)
        run_git(path, "init", "-q", "-b", "main")
        for rel, content in (files or {"README.md": "hi"}).items():
            write(os.path.join(path, rel), content)
        if commit:
            run_git(path, "add", "-A")
            run_git(path, "commit", "-q", "-m", "init")
        return path
    return _make


@pytest.fixture
def make_session():
    def _make(claude_dir, cwd, sid, records, mtime=None):
        d = os.path.join(str(claude_dir), "projects", cwd.replace("/", "-"))
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, sid + ".jsonl")
        with open(fp, "w") as f:
            for r in records:
                f.write(r if isinstance(r, str) else json.dumps(r, separators=(",", ":")))
                f.write("\n")
        if mtime:
            os.utime(fp, (mtime, mtime))
        return fp
    return _make


def user(cwd, text, entry="cli"):
    return {"type": "user", "cwd": cwd, "entrypoint": entry,
            "message": {"role": "user", "content": text}}


@pytest.fixture
def fake_ssh(monkeypatch, tmp_path):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    script = bindir / "ssh"
    script.write_text(
        "#!/bin/sh\n"
        "while [ \"$1\" = -o ] || [ \"$1\" = -t ]; do\n"
        "  if [ \"$1\" = -o ]; then shift; fi; shift\n"
        "done\n"
        "[ \"$1\" = unreachable ] && { echo 'ssh: connect failed' >&2; exit 255; }\n"
        "shift\n"
        "exec sh -c \"$*\"\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("HOME", str(tmp_path / "isolated_home"))  # never scan the real ~/.claude
    cache = tmp_path / "cache"
    monkeypatch.setenv("PROJ_CACHE", str(cache))
    return str(cache)
