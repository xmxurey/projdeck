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


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Never read the real ~ (e.g. ~/PROJECTS.md); tests needing a home set HOME again."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))


@pytest.fixture(autouse=True)
def no_real_claude(tmp_path_factory, monkeypatch):
    """`claude agents --json` must never reach the real CLI, here or in child processes
    (fake ssh): put a stub that prints [] first on PATH."""
    d = tmp_path_factory.mktemp("fakeclaude")
    stub = d / "claude"
    stub.write_text("#!/bin/sh\necho '[]'\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ["PATH"])


@pytest.fixture(autouse=True)
def no_real_zellij_or_ssh(tmp_path_factory, monkeypatch):
    """Neither `zellij` nor `ssh` may reach the real program: stand-ins come first on PATH.
    zellij answers every call with failure (= "not usable"); ssh fails with 255. Both record
    their arguments, one call per line, in <dir>/calls (env PROJ_STUB_CALLS). Tests that need
    a working one put their own stand-in in front."""
    d = tmp_path_factory.mktemp("fakeremote")
    calls = d / "calls"
    for prog, code in (("zellij", 1), ("ssh", 255)):
        stub = d / prog
        stub.write_text('#!/bin/sh\necho "%s $*" >> "%s"\nexit %d\n' % (prog, calls, code))
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PROJ_STUB_CALLS", str(calls))


@pytest.fixture(autouse=True)
def clean_cli_notices():
    """cli collects refresh problems in a module-level list; no test may inherit another's."""
    from projdeck import cli
    del cli._notices[:]
    yield
    del cli._notices[:]


@pytest.fixture(autouse=True)
def live_sync_spawns(monkeypatch):
    """cli.background_live_sync must never start a real `proj sync --live`: replace it with a
    recorder; the list of calls is available by requesting this fixture."""
    from projdeck import cli
    calls = []
    monkeypatch.setattr(cli, "background_live_sync", lambda *a, **k: calls.append(1))
    return calls


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
