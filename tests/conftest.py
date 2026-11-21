import faulthandler
import json
import os
import subprocess

import pytest

from tests import _stubs

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


# Programs the tests must never reach for real (shared with scripts/sandbox.py); every
# stand-in records its arguments, one call per line, in <dir>/calls (env PROJ_STUB_CALLS).
REAL_PROGRAM_STUBS = _stubs.REAL_PROGRAM_STUBS

# A test running longer than this many seconds is taken as hung: every thread's stack goes
# to stderr and the whole run ends (exit 1) instead of waiting forever. 0 turns it off.
TEST_TIMEOUT = float(os.environ.get("PROJ_TEST_TIMEOUT", "300"))
_real_stderr = None


def pytest_configure(config):
    # fd 2 before output capture takes it over, so a hang report reaches the terminal
    global _real_stderr
    _real_stderr = os.fdopen(os.dup(2), "w")


@pytest.fixture(autouse=True)
def hang_watchdog():
    if TEST_TIMEOUT <= 0:
        yield
        return
    faulthandler.dump_traceback_later(TEST_TIMEOUT, exit=True, file=_real_stderr)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


@pytest.fixture(autouse=True)
def no_real_programs(tmp_path_factory, monkeypatch):
    """None of REAL_PROGRAM_STUBS may reach the real program, here or in child processes:
    stand-ins come first on PATH. Tests that need a working one put their own in front."""
    d = tmp_path_factory.mktemp("fakeremote")
    cdir = tmp_path_factory.mktemp("fakeclaude")  # claude lives apart: tests inspect its dir name
    calls = d / "calls"
    front = _stubs.install(d, cdir, calls)
    monkeypatch.setenv("PATH", os.pathsep.join(front + [os.environ["PATH"]]))
    monkeypatch.setenv("PROJ_STUB_CALLS", str(calls))
    monkeypatch.setenv("PROJ_STUB_DIRS", os.pathsep.join([str(d), str(cdir)]))


@pytest.fixture
def real_path():
    """PATH without the stand-in directories; for the PROJ_LIVE_TESTS=1 tests only.
    Returns the string; the caller decides where to use it."""
    stubs = os.environ["PROJ_STUB_DIRS"].split(os.pathsep)
    return os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if p not in stubs)


@pytest.fixture
def real_which(real_path):
    """real_which(prog) -> path of the real program, or skip the test, naming what is missing."""
    import shutil

    def _which(prog):
        found = shutil.which(prog, path=real_path)
        if not found:
            pytest.skip("PROJ_LIVE_TESTS=1 but no real %s on PATH" % prog)
        return found
    return _which


@pytest.fixture(autouse=True)
def isolated_proj_dirs(tmp_path_factory, monkeypatch):
    """Cache and config never come from the outer shell: a tmp cache dir and a config path
    that does not exist. Tests that need others set them again."""
    d = tmp_path_factory.mktemp("projdirs")
    monkeypatch.setenv("PROJ_CACHE", str(d / "cache"))
    monkeypatch.setenv("PROJ_CONFIG", str(d / "no-config.json"))


@pytest.fixture(autouse=True)
def clean_cli_notices():
    """cli collects refresh problems in a module-level list; no test may inherit another's."""
    from projdeck import cli
    del cli._notices[:]
    cli._usage_warned = False
    yield
    del cli._notices[:]
    cli._usage_warned = False


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
