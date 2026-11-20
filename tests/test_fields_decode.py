"""The decoding side of the hidden fzf fields: every picker and preview that reads a path or a
session id back from a line must pass it through ui.from_field, or a non-UTF-8 name comes back
as `hex:...` (and cmd_grep / the previews then fail to find the project)."""
import json
import time
from types import SimpleNamespace

import pytest

from projdeck import cli, sync, ui
from tests.test_fields_spec import BAD, CFG, NOW, proj, row

BAD_SID = "s\udcff1"
BACKLOG = {"path": "docs/backlog.md", "mtime": 5, "open": 1, "done": 0, "raw": "- [ ] ship it\n",
           "items": [{"title": "ship it", "section": "now", "done": False, "text": "ship the thing"}]}


def _row():
    r = row(host="mac", path=BAD, backlog=BACKLOG)
    r["sessions"] = [dict(r["sessions"][0], id=BAD_SID, cwd=BAD)]
    return r


def _pick_first_data_line(monkeypatch, index):
    """fzf stand-in: returns the line at `index` of what it was given, as fzf would."""
    seen = {}

    def fake_fzf(lines, args, expect=()):
        seen["lines"] = lines
        return "", [lines[index]]
    monkeypatch.setattr(ui, "run_fzf", fake_fzf)
    return seen


def test_pick_backlog_returns_the_raw_path(monkeypatch):
    seen = _pick_first_data_line(monkeypatch, 1)  # line 0 is the project header
    assert ui.pick_backlog([_row()], NOW) == ("mac", BAD)
    assert all(ln.isascii() for ln in seen["lines"][1].split("\t")[:3])  # what fzf saw is encoded


def test_pick_session_returns_the_raw_path_and_session_id(monkeypatch):
    r = _row()
    seen = _pick_first_data_line(monkeypatch, 0)
    assert ui.pick_session([(r, r["sessions"][0])], NOW) == ("mac", BAD, BAD_SID)
    assert seen["lines"][0].split("\t")[1].startswith(ui.HEX_MARK)


def test_pick_prompt_returns_the_raw_path_and_session_id(monkeypatch):
    r = _row()
    seen = _pick_first_data_line(monkeypatch, 0)
    assert ui.pick_prompt([(r, r["sessions"][0], "fix it")], NOW) == ("mac", BAD, BAD_SID)
    assert seen["lines"][0].split("\t")[2].startswith(ui.HEX_MARK)


def test_spreview_finds_the_session_of_a_non_utf8_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    r = _row()
    p = proj(BAD, sessions=[dict(r["sessions"][0], last_ts=time.time() - 60, title="the-title")])
    p["sessions"][0]["id"] = BAD_SID
    sync._write_cache("vm", {"schema": 1, "host": "vm", "collected_at": time.time(),
                             "synced_at": time.time(), "projects": [p]})
    args = SimpleNamespace(host="vm", path=ui.to_field(BAD) + "\t", sid=ui.to_field(BAD_SID) + "\t",
                           prompt="\t")
    assert cli.cmd_spreview(CFG, args) == 0
    out = capsys.readouterr().out
    assert "the-title" in out and "(not in cache)" not in out
