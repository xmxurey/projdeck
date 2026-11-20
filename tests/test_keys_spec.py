"""T25 spec tests: ^S -> ^Y rename, PROJECT_KEYS / PROJECT_HEADER, README key table.

cmd_browse is driven through a fake `fzf` executable on PATH (the fzf --expect protocol:
first output line is the pressed key, then the selected line); open/launch are recorded.
"""
import os
import re
import time

import pytest

from projdeck import cli, config, ui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KEYS = ["ctrl-r", "ctrl-y", "ctrl-f", "ctrl-x", "ctrl-u", "ctrl-l", "ctrl-v"]
HEADER = ("Enter: resume · ^R: resume latest · ^Y: pick session · ^F: search prompts · "
          "^X: clean · ^U: sync · ^L: backlog · ^V: dashboard")

FAKE_FZF = r"""#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_FZF_DIR"]
n = len([f for f in os.listdir(d) if f.startswith("call")])
open(os.path.join(d, "call%d" % n), "w").write("\n".join(sys.argv[1:]))
lines = [l for l in sys.stdin.read().split("\n") if l]
script = [x for x in os.environ.get("FAKE_SCRIPT", "").split(";") if x != ""]
if n >= len(script):
    if n > len(script) + 5:  # cmd_browse keeps relisting: break the loop for good
        import signal
        os.kill(os.getppid(), signal.SIGINT)
    sys.exit(130)  # Esc
key, _, pick = script[n].partition("|")
if key == "-":
    key = ""
pick = int(pick or 0)
if n == 0:  # the project row to select (not the host header line); /p/a unless told otherwise
    want = "\t%s\t" % os.environ.get("FAKE_PICK_PATH", "/p/a")
    pick = [i for i, l in enumerate(lines) if want in l][0]
sys.stdout.write(key + "\n" + lines[pick] + "\n")
"""


def test_keys_and_header_exact():
    assert ui.PROJECT_KEYS == KEYS
    assert ui.PROJECT_HEADER == HEADER
    assert "ctrl-s" not in ui.PROJECT_KEYS and "^S" not in ui.PROJECT_HEADER


def test_readme_key_table():
    text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "^Y pick session" in text
    assert "^S" not in text
    assert "^L backlog" in text and "^V dashboard" in text


@pytest.fixture
def browse(tmp_path, monkeypatch):
    now = int(time.time())
    fdir = tmp_path / "fzfcalls"
    fdir.mkdir()
    bindir = tmp_path / "fzfbin"
    bindir.mkdir()
    fz = bindir / "fzf"
    fz.write_text(FAKE_FZF)
    fz.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_FZF_DIR", str(fdir))
    cfg = config.load_config("/nonexistent")
    host = cfg["hosts"][0]["name"]
    sess = [{"id": "sess-new", "last_ts": now - 100, "cwd": "/p/a", "first_prompt": "newer",
             "prompts": [], "title": "t", "entrypoint": "cli"},
            {"id": "sess-old", "last_ts": now - 5000, "cwd": "/p/a", "first_prompt": "older",
             "prompts": [], "title": "t", "entrypoint": "cli"}]
    sess_b = [{"id": "sess-b", "last_ts": now - 9000, "cwd": "/p/b", "first_prompt": "bee",
               "prompts": [], "title": "t", "entrypoint": "cli"}]
    cache = {"host": host, "synced_at": now, "stale_since": None, "home": "/h", "tags": [],
             "projects": [{"path": "/p/a", "name": "a", "exists": True, "last_mtime": now - 50,
                           "sessions": sess},
                            {"path": "/p/b", "name": "b", "exists": True, "last_mtime": now - 9000,
                             "sessions": sess_b}]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg_, now_: [cache])
    monkeypatch.setattr(cli, "background_sync", lambda *a, **k: None)
    calls = {"open": [], "launch": []}
    monkeypatch.setattr(cli, "open_project", lambda rows, r: (calls["open"].append(r), ("here", None))[1])
    monkeypatch.setattr(cli, "launch",
                        lambda row, path, session=None: (calls["launch"].append(session), "here")[1])

    def run(key, pick2=0, pick_path="/p/a"):
        # call 0: the list with `key`; call 1 (only for ctrl-y): the session picker; then Esc
        script = "%s|0" % (key or "-")
        if key == "ctrl-y":
            script += ";-|%d" % pick2
        monkeypatch.setenv("FAKE_SCRIPT", script)
        monkeypatch.setenv("FAKE_PICK_PATH", pick_path)
        import argparse
        try:
            cli.cmd_browse(cfg, argparse.Namespace(**{k: None for k in
                           ("project", "host", "all", "tier", "query")}))
        except (SystemExit, KeyboardInterrupt):
            pass
        return calls

    run.fdir = fdir
    run.calls = calls
    return run


def fzf_args(browse):
    return open(os.path.join(str(browse.fdir), "call0")).read().split("\n")


def test_expect_passed_to_fzf(browse):
    browse("")
    args = " ".join(fzf_args(browse))
    m = re.search(r"--expect[= ]?(\S+)", args)
    assert m, args
    keys = m.group(1).split(",")
    for k in ("ctrl-y", "ctrl-l", "ctrl-v"):
        assert k in keys
    assert "ctrl-s" not in keys and "ctrl-s" not in args


def _acted(calls):
    return calls["open"] or calls["launch"]


def test_ctrl_y_enters_session_picker_and_resumes_selected(browse):
    calls = browse("ctrl-y", pick2=1)
    assert [x["id"] for x in calls["launch"]] == ["sess-old"], calls
    assert not calls["open"]
    # a second fzf invocation (the session picker) happened
    assert os.path.exists(os.path.join(str(browse.fdir), "call1"))


def test_ctrl_y_first_session(browse):
    calls = browse("ctrl-y", pick2=0)
    assert [x["id"] for x in calls["launch"]] == ["sess-new"]


@pytest.mark.parametrize("key", ["ctrl-s", "ctrl-z"])
def test_unbound_or_pending_keys_do_nothing(browse, key):
    calls = browse(key)
    assert not _acted(calls), calls
    # the unbound key sends us back to the list: fzf must be called a second time, and what it
    # shows is the relisted overview, not a session picker
    f = os.path.join(str(browse.fdir), "call1")
    assert os.path.exists(f), "no second fzf call after %s" % key
    assert "--expect=" + ",".join(KEYS) in open(f).read().split("\n")


@pytest.mark.parametrize("path", ["/p/a", "/p/b"])
def test_enter_opens_the_selected_project(browse, path):
    calls = browse("", pick_path=path)
    assert [r["path"] for r in calls["open"]] == [path], calls
    assert not calls["launch"]
