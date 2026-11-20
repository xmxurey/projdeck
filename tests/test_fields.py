"""Hidden fzf fields: non-UTF-8 projects work in the list, the preview subprocesses, the
cursor restore, Enter and the usage log."""
import json
import os
import re
import subprocess
import sys
import time

import pytest

from projdeck import cli, model, sync, ui
from projdeck.ui import from_field, to_field

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAD = "/home/u/dev/b\udcffd"
NOW = time.time()


def is_ascii(s):
    return s.isascii()


@pytest.mark.parametrize("x", ["", "/home/u/p", "/home/u/中文", BAD, "\udcff", "hex:", "hex:abc",
                               "hex:" + BAD, "a\udc80\udcff中", "\ud800x"])
def test_roundtrip_and_ascii(x):
    enc = to_field(x)
    if "\ud800" in x:  # not producible by a file system: replaced, no exception
        assert from_field(enc) == x.replace("\ud800", "�")
    else:
        assert from_field(enc) == x
    assert not re.search("[\ud800-\udfff]", enc)
    if "\udcff" in x or "\udc80" in x or x.startswith("hex:"):
        assert is_ascii(enc) and enc.startswith("hex:")


def test_plain_and_cjk_paths_are_left_alone():
    assert to_field("/home/u/中文") == "/home/u/中文"
    assert to_field("/home/u/p") == "/home/u/p"


def row(path=BAD, host="s-host", **kw):
    r = dict(model.ROW_DEFAULTS, host=host, path=path, tier="active", activity=NOW - 10,
             exists=True, local=True, ssh=host, tags=[], home="/home/u", sessions=[
                 {"id": "sid\udcff1", "title": "t", "last_ts": NOW - 5}])
    r.update(kw)
    return r


def test_lines_hide_only_ascii_for_a_bad_path():
    r = row()
    for line in (ui.project_lines([r], NOW)[1], ui.session_line(r, r["sessions"][0], NOW),
                 ui.prompt_line(r, r["sessions"][0], "hi", NOW),
                 ui.file_line({"path": "f\udcff.log", "suspicious": True, "size": 10}),
                 cli._batch_line({"name": "h"}, {"id": "b\udcff", "created": NOW, "count": 1,
                                                 "project": BAD})):
        hidden = line.split("\t")[:-1]
        assert all(is_ascii(f) for f in hidden), line
    cjk = ui.project_lines([row("/home/u/中文")], NOW)[1]
    assert cjk.split("\t")[1] == "/home/u/中文"


def test_pick_project_puts_the_cursor_on_the_bad_project(monkeypatch):
    rows = [row("/home/u/a"), row(BAD), row("/home/u/z")]
    seen = {}

    def fzf(lines, args, expect=()):
        seen["lines"], seen["args"] = lines, args
        return "", [lines[-1]]
    monkeypatch.setattr(ui, "run_fzf", fzf)
    _, host, path = ui.pick_project(rows, NOW, select=("s-host", BAD))
    n = next(i for i, ln in enumerate(seen["lines"], 1) if ln.split("\t")[1] == to_field(BAD))
    assert "--bind=load:pos(%d)" % n in seen["args"]
    ui.pick_project(rows, NOW)
    monkeypatch.setattr(ui, "run_fzf", lambda lines, args, expect=(): ("", [next(
        ln for ln in lines if ln.split("\t")[1] == to_field(BAD))]))
    assert ui.pick_project(rows, NOW)[2] == BAD  # the picked line comes back as the real path


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache, conf = tmp_path / "cache", tmp_path / "conf.json"
    cache.mkdir()
    monkeypatch.setenv("PROJ_CACHE", str(cache))
    monkeypatch.setenv("PROJ_CONFIG", str(conf))
    conf.write_text(json.dumps({"hosts": [{"name": "s-host", "local": True}]}))
    proj = {"path": BAD, "exists": True, "last_mtime": NOW - 100, "sessions": [], "errors": [],
            "suspicious": [], "git": None}
    sync._write_atomic(str(cache / "s-host.json"), json.dumps(
        {"host": "s-host", "synced_at": NOW, "stale_since": None, "projects": [proj]}))
    return tmp_path


def test_preview_subprocess_finds_the_bad_project(env):
    out = subprocess.run([sys.executable, "-m", "projdeck", "_preview", "s-host", to_field(BAD) + "\t"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=REPO,
                         env=dict(os.environ, PYTHONPATH=REPO), check=True)
    text = out.stdout.decode("utf-8")
    assert "�" in text and "(not in cache)" not in text


def test_fpreview_reads_a_non_utf8_file_name(env, tmp_path, capsys):
    root = tmp_path / "root"
    root.mkdir()
    rel = "f\udcff.log"
    with open(os.path.join(str(root), rel), "wb"):
        pass
    assert cli.main(["_fpreview", "s-host", str(root), to_field(rel)]) == 0
    assert "cannot preview" not in capsys.readouterr().out


def test_pick_files_returns_the_real_name(monkeypatch):
    f = {"path": "f\udcff.log", "suspicious": False, "size": 1}
    monkeypatch.setattr(ui, "run_fzf", lambda lines, args, expect=(): ("", list(lines)))
    assert ui.pick_files([f]) == ["f\udcff.log"]


def test_restore_gets_the_real_batch_id(env, monkeypatch):
    got = {}
    monkeypatch.setattr(cli, "_all_batches", lambda cfg, h: ([({"name": "s-host"}, {
        "id": "b\udcff", "created": NOW, "count": 1, "project": BAD})], []))
    monkeypatch.setattr(ui, "pick_line", lambda lines, header, **k: lines[0])
    monkeypatch.setattr(sync, "run_agent", lambda host, req, t=20: got.update(req) or
                        {"restored": [], "skipped": []})
    assert cli.main(["restore"]) == 0
    assert got["batch"] == "b\udcff"


@pytest.mark.parametrize("mux", ["tmux", "zellij"])
def test_enter_on_a_bad_project_and_the_report(env, monkeypatch, mux):
    fake = env / "bin"
    fake.mkdir()
    (fake / "zellij").write_text("#!/bin/sh\nexit 0\n")  # list-sessions: no sessions
    (fake / "zellij").chmod(0o755)
    monkeypatch.setenv("PATH", str(fake) + os.pathsep + os.environ["PATH"])
    r = row(mux=mux, local=True)
    monkeypatch.delenv("ZELLIJ", raising=False)
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
    ran = []
    monkeypatch.setattr(cli.opener, "open_command", lambda cmd, title, run=None, iterm=None:
                        ran.append(cmd) or "inline")
    monkeypatch.setattr(cli.opener, "has_iterm", lambda: False)
    outcome, _ = cli.open_project([r], r)
    assert outcome == "inline" and ran
    if mux == "zellij":
        assert any("zellij" in c for c in ran)
    log = os.path.join(sync.cache_dir(), "usage.jsonl")
    with open(log, "ab") as f:
        f.write(b'{"ts": 1, "x": "\xff"}\n')
    with open(log, "rb") as f:
        lines = f.read().split(b"\n")
    assert all(ln.isascii() for ln in lines[:-2]) and lines[:-2]
    assert cli.main(["report"]) == 0


def test_tmux_name_hashes_a_non_utf8_path():
    assert cli.opener.tmux_name(BAD).startswith("proj-")


def test_log_event_never_fails_the_command(env):
    cli.log_event("open", "s-host", "\ud800")  # not encodable at all as utf-8: still ASCII
    with open(os.path.join(sync.cache_dir(), "usage.jsonl"), "rb") as f:
        assert f.read().isascii()


def test_ui_has_no_surrogate_pattern_of_its_own():
    """One definition of "holds a lone surrogate": termtext's."""
    from projdeck import termtext, ui
    assert not hasattr(ui, "_SURROGATE_RE")
    assert termtext.has_surrogates("a\udcffb") and not termtext.has_surrogates("a�b")
    assert ui.to_field("a\udcffb").startswith(ui.HEX_MARK)
