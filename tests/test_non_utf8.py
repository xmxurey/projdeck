"""Non-UTF-8 names (surrogateescape strings) must not crash any outlet."""
import sys
from pathlib import Path

import pytest

from projdeck import board, model, report, termtext
from tests.test_web_serve import NOW, request, running, sess  # noqa: F401 (fixture)

BAD = "/home/u/dev/b\udcffd"
INVISIBLE = "؜​‎‏‪‫‬‭‮⁦⁧⁨⁩﻿"


def cache_with_bad_path():
    return [{"host": "s-host", "synced_at": NOW - 5, "stale_since": None, "projects": [
        {"path": BAD, "exists": True, "last_mtime": NOW - 100, "sessions": [],
         "git": {"branch": "m", "dirty": 1, "ahead": 0, "no_upstream": True,
                 "last_commit_ts": NOW - 50}, "errors": [], "suspicious": ["f\udcff.log"]}]}]


CFG = {"hosts": [{"name": "s-host", "local": True}], "tiers_days": {"active": 14, "stale": 90}}


def test_no_surrogates_replaces_the_whole_range():
    assert termtext.no_surrogates("a\udcffb\ud800c\udfffd") == "a�b�c�d"
    assert termtext.no_surrogates("plain 中文") == "plain 中文"


@pytest.mark.parametrize("fn", [termtext.safe, termtext.one_line])
def test_safe_and_one_line_drop_surrogates_and_invisible_controls(fn):
    out = fn("a\udcff\ud800" + INVISIBLE + "b")
    assert out == "a��b"
    out.encode("utf-8")
    assert fn("x‍y") == "x‍y"
    assert fn("a‌b") == "a‌b"


def test_has_control_flags_each_of_them():
    for ch in ("\udcff", "\ud800", "‮", "​", "﻿", "؜", "⁦"):
        assert termtext.has_control("a" + ch + "b"), repr(ch)
    assert not termtext.has_control("a‍b")


def test_report_render_is_strict_utf8():
    caches = cache_with_bad_path()
    rows = model.rows(caches, CFG, NOW)
    events = [{"ts": NOW, "action": "open", "host": "s-host", "path": BAD}]
    md = report.render(rows, caches, NOW, events=events, board=board.build(caches, [], CFG, NOW),
                       lives=[], cfg=CFG)
    md.encode("utf-8")
    assert "b�d" in md


@pytest.mark.parametrize("path", ["/", "/projects", "/api/state.json"])
def test_serve_answers_200_for_a_non_utf8_project(running, path):  # noqa: F811
    caches = cache_with_bad_path()
    snap = board.build(caches, [], CFG, NOW)
    # the board page lists sessions by project, so give it one that lives in the bad path
    snap["waiting"] = [dict(sess("a", "waiting"), project=BAD, name="n\udcff")]
    port, _ = running(snapshot_fn=lambda: snap)
    status, _, body = request(port, path)
    assert status == 200
    text = body.decode("utf-8")
    assert "b�d" in text
