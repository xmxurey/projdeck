"""F7: every terminal outlet strips control characters; web shows no command for an altered name."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from projdeck import board, cli, model, report, sync, termtext, ui, web

NOW = 1_800_000_000
OSC52 = "\x1b]52;c;ZXZpbA==\x07"
CTRL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "contrib" / "statusline-waiting.sh"


def no_ctrl(text):
    """No control character except newline, and no ESC at all."""
    return not CTRL.search(text)


# ---- one shared module

def test_shared_module_is_what_ui_and_report_use():
    assert ui._safe is termtext.safe and ui._one_line is termtext.one_line
    assert termtext.safe("a" + OSC52 + "\tb\nc\x9b") == "a b\nc"
    assert termtext.one_line("a\n b\x1b[31m  c") == "a b c"
    assert termtext.has_control("a\nb") and termtext.has_control("a\x1bb")
    assert not termtext.has_control("proj-ok name")


def test_ui_does_not_import_the_web_module():
    code = "import sys, projdeck.ui; print('projdeck.web' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == "False"


# ---- status line

def live(sessions, **kw):
    d = {"schema": 1, "host": "s-host", "collected_at": NOW - 5, "source": "claude agents",
         "sessions": sessions, "projects": {}, "errors": []}
    d.update(kw)
    return d


def waiting(project, name="n"):
    return {"id": "w", "project": project, "live": True, "state": "waiting", "name": name,
            "idle_since": NOW - 100, "segments": [], "attach": "", "errors": []}


CFG = {"hosts": [{"name": "s-host", "local": True, "mux": "zellij"}]}


def test_statusline_doc_project_and_name_are_one_clean_line():
    b = board.build([], [live([waiting("/home/u/ev" + OSC52 + "il\nx", name="a\x1b[2Jb")])], CFG, NOW)
    w = board.statusline_doc(b, NOW)["waiting"][0]
    assert w["project"] == "evil x" and w["name"] == "ab"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_statusline_segment_strips_control_characters(tmp_path):
    (tmp_path / "live").mkdir()
    import time
    (tmp_path / "live" / "statusline.state").write_text(json.dumps(
        {"updated": time.time(), "waiting": [{"project": "a" + OSC52 + "b\nc\x9bd",
                                              "session_id": "x"}]}))
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PROJ_CACHE": str(tmp_path)}
    out = subprocess.run(["bash", "-c", '. "%s"; proj_waiting_segment me' % SCRIPT], env=env,
                         capture_output=True, text=True).stdout
    body = out.replace("\x1b[38;2;217;89;38m", "").replace("\x1b[0m", "")
    assert body == "◆ 等你 1 · a]52;c;ZXZpbA==bcd"
    assert "\n" not in out


# ---- proj report

def test_report_strips_paths_errors_and_file_names():
    bad = "/home/u/x" + OSC52
    caches = [{"host": "s-host", "synced_at": NOW - 5, "stale_since": NOW - 5,
               "last_error": "ssh: " + OSC52 + "boom", "projects": [
                   {"path": bad, "exists": True, "last_mtime": NOW - 100, "sessions": [],
                    "git": {"branch": "m", "dirty": 1, "ahead": 0, "no_upstream": True,
                            "last_commit_ts": NOW - 50}, "errors": [],
                    "suspicious": ["f" + OSC52 + ".log"]}]}]
    cfg = {"hosts": [{"name": "s-host", "local": True}], "tiers_days": {"active": 14, "stale": 90}}
    rows = model.rows(caches, cfg, NOW)
    events = [{"ts": NOW, "action": "open", "host": "s-host", "path": bad}]
    md = report.render(rows, caches, NOW, events=events, board=board.build(caches, [], cfg, NOW),
                       lives=[], cfg=cfg)
    assert no_ctrl(md)
    assert "boom" in md and "`s-host:/home/u/x`" in md and "f.log" in md


# ---- cli's own prints

def test_print_sync_strips_the_error(capsys):
    cli.print_sync([{"host": "h", "ok": False, "error": "x" + OSC52 + "\ny"}])
    out = capsys.readouterr().out
    assert out == "h: FAILED (x y)\n"


def test_main_strips_agent_errors(monkeypatch, capsys):
    monkeypatch.setattr(cli.config, "load_config", lambda: {"hosts": []})

    def boom(cfg, args):
        raise sync.AgentError("remote said " + OSC52)
    monkeypatch.setitem(cli.COMMANDS, "sync", boom)
    assert cli.main(["sync"]) == 2
    assert no_ctrl(capsys.readouterr().err)


def test_clean_prints_file_names_stripped(monkeypatch, capsys):
    cfg = {"hosts": [{"name": "h", "local": True}], "suspicious_patterns": None,
           "trash_dir": "~/t", "ssh_timeout": 5}
    bad = "a" + OSC52 + ".log"

    def agent(host, req, timeout):
        if req["action"] == "list":
            return {"path": "/p", "files": [{"path": bad, "suspicious": True, "size": 1}]}
        return {"moved": [], "skipped": [bad], "batch": None}
    monkeypatch.setattr(cli.sync, "run_agent", agent)
    monkeypatch.setattr(cli.ui, "pick_files", lambda files, preview=None: [bad])
    monkeypatch.setattr(cli, "_ask", lambda prompt: "y")
    cli.clean(cfg, "h", "/p" + OSC52)
    assert no_ctrl(capsys.readouterr().out)


def test_batch_lines_are_stripped():
    # F14: the shown text is clean; the picker line keeps the raw id in a hidden field
    b = {"id": "b" + OSC52, "created": NOW, "count": 1, "project": "/p" + OSC52}
    assert no_ctrl(cli._batch_text({"name": "h"}, b))
    assert no_ctrl(cli._batch_line({"name": "h"}, b).split("\t", 2)[2])


# ---- preview branch name

def test_preview_strips_the_branch_name():
    r = dict(model.ROW_DEFAULTS, host="h", path="/p", tier="active", activity=NOW - 10,
             git={"branch": "main\x9b31m" + OSC52, "dirty": 0, "ahead": 0, "untracked": 0,
                  "no_upstream": False, "last_commit_ts": NOW - 10})
    text = ui.preview_text(r, NOW)
    assert no_ctrl(text) and "git: main31m" in text


# ---- exited zellij names altered by cleaning: no command

def test_exited_name_with_control_characters_gets_no_command():
    b = board.build([], [live([], zellij_exited=["ok", "bad\x1b[31m", "nl\nx"])], CFG, NOW)
    by = {e["name"]: e for e in b["summary"]["zellij_exited"]}
    assert by["ok"]["cmd"] and by["bad\x1b[31m"]["cmd"] is None and by["nl\nx"]["cmd"] is None
    html = web.render_header(b)
    assert html.count("zellij delete-session") == 1
    assert html.count(web.h(board.UNSAFE_NAME)) == 2
    assert report.UNSAFE_NAME == board.UNSAFE_NAME
