#!/usr/bin/env python3
"""Run a probe or debug script with your real proj environment out of reach.

Inside pytest, tests/conftest.py already isolates everything. Outside it (a one-off
`python3 -c ...`, a script in a scratch directory, `python3 -m projdeck report` to see what
a change prints), nothing does: proj would read your real config, append to your real
usage log and ssh to real hosts. Run such commands through here instead:

    python3 scripts/sandbox.py [--config FIXTURE.json] [--keep] -- CMD [ARGS...]

What the command gets:
  - HOME, PROJ_CACHE and PROJ_CONFIG inside a fresh temp directory (PROJ_CONFIG is the
    --config fixture copied in, or a path that does not exist);
  - stand-ins first on PATH for claude, ssh, zellij, tmux, fzf, systemctl and the rest of
    tests/_stubs.REAL_PROGRAM_STUBS, each logging its calls;
  - PYTHONUSERBASE still pointing at your real user site, so a `pip --user` pytest is found
    even though HOME moved;
  - PROJ_LIVE_TESTS removed.
The exit code is the command's. The temp directory is deleted afterwards unless --keep.
"""
import argparse
import os
import shutil
import site
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests import _stubs  # noqa: E402


def sandbox_env(base, box, config=None):
    """The environment for a command run in box (an empty directory), derived from base."""
    env = dict(base)
    env.pop("PROJ_LIVE_TESTS", None)
    # the real user site, computed before HOME moves
    env["PYTHONUSERBASE"] = base.get("PYTHONUSERBASE") or site.getuserbase()
    home = os.path.join(box, "home")
    os.makedirs(home)
    env["HOME"] = home
    env["PROJ_CACHE"] = os.path.join(box, "cache")
    env["PROJ_CONFIG"] = os.path.join(box, "config.json" if config else "no-config.json")
    if config:
        shutil.copyfile(config, env["PROJ_CONFIG"])
    stub_dir, claude_dir = os.path.join(box, "stubs"), os.path.join(box, "claude-bin")
    os.makedirs(stub_dir)
    os.makedirs(claude_dir)
    calls = os.path.join(box, "calls")
    front = _stubs.install(stub_dir, claude_dir, calls)
    env["PATH"] = os.pathsep.join(front + [base.get("PATH", os.defpath)])
    env["PROJ_STUB_CALLS"] = calls
    env["PROJ_SANDBOX"] = box
    return env


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sandbox.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", help="fixture config to use as PROJ_CONFIG (never your real one)")
    ap.add_argument("--keep", action="store_true", help="keep the temp directory")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        ap.error("no command given (put it after --)")
    box = tempfile.mkdtemp(prefix="proj-sandbox-")
    try:
        env = sandbox_env(os.environ, box, a.config)
        rc = subprocess.call(cmd, env=env)
        try:
            with open(env["PROJ_STUB_CALLS"]) as f:
                calls = f.read().splitlines()
        except OSError:
            calls = []
        print("sandbox: %d stand-in call(s)%s" % (
            len(calls), "".join("\n  " + c for c in calls)), file=sys.stderr)
        return rc
    finally:
        if a.keep:
            print("sandbox: kept %s" % box, file=sys.stderr)
        else:
            shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
