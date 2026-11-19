"""F4: stale data never reads as fresh: status-line updated, snapshot age, missing times."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from projdeck import board, ui, web
from tests.test_web_serve import make_board, request, running  # noqa: F401 (fixture)

NOW = 1_800_000_000
P = "/home/u/alpha"
CFG = {"hosts": [{"name": "s-host", "local": True}, {"name": "em", "ssh": "em"}]}
SCRIPT = Path(__file__).resolve().parent.parent / "contrib" / "statusline-waiting.sh"


def sess(sid, state, **kw):
    s = {"id": sid, "project": P, "kind": "interactive", "live": True, "name": sid, "title": sid,
         "started_at": NOW - 5000, "status": "idle", "state": state, "idle_since": NOW - 1000,
         "question": "", "subagents_running": 0, "segments": [], "attach": "", "errors": []}
    s.update(kw)
    return s


def live(host, sessions, at, **kw):
    d = {"schema": 1, "host": host, "collected_at": at, "source": "claude agents",
         "sessions": sessions, "projects": {}, "errors": []}
    d.update(kw)
    return d


# ---- status line: updated is when local live last succeeded

def test_statusline_updated_is_local_live_time_not_now():
    b = board.build([], [live("s-host", [sess("w", "waiting")], at=NOW - 400)], CFG, NOW)
    doc = board.statusline_doc(b, NOW)
    assert doc["updated"] == NOW - 400


def test_statusline_updated_never_in_the_future():
    b = board.build([], [live("s-host", [], at=NOW + 50)], CFG, NOW)
    assert board.statusline_doc(b, NOW)["updated"] == NOW


def test_statusline_updated_missing_without_local_live():
    b = board.build([], [live("em", [sess("w", "waiting")], at=NOW - 10)], CFG, NOW)
    assert board.statusline_doc(b, NOW)["updated"] is None


# ---- snapshot age on the web page

def test_board_carries_built_at():
    assert board.build([], [], CFG, NOW)["built_at"] == NOW


def fresh_board():
    return board.build([], [live("s-host", [], at=NOW - 2)], CFG, NOW)


def test_page_rendered_long_after_build_says_board_stopped():
    html = web.render(fresh_board(), "home", fragment=True, now=NOW + 61)
    assert "看板停止刷新" in html and "board-stale" in html
    assert "s-host 实时" not in html  # chip ages count from the request, not the snapshot


def test_page_rendered_soon_after_build_is_not_stale():
    assert "看板停止刷新" not in web.render(fresh_board(), "home", fragment=True, now=NOW + 60)
    assert "s-host 实时" in web.render(fresh_board(), "home", fragment=True, now=NOW + 20)


def test_render_without_now_uses_the_snapshot_time():
    assert "s-host 实时" in web.render(fresh_board(), "home", fragment=True)


def test_served_page_uses_request_time(running):
    b = make_board()
    b["built_at"] = time.time() - 300
    port, _ = running(lambda: b)
    st, _, body = request(port, "/fragment/home")
    assert st == 200 and "看板停止刷新" in body.decode()


# ---- missing times are "—"

def test_chip_without_any_data_time_does_not_say_zero_minutes():
    hosts = [{"name": "em", "live_at": None, "collect_at": None, "stale_since": NOW - 5,
              "error": "ssh: connect failed"}]
    html = web.render_header({"hosts": hosts, "summary": {}, "lanes": {"end": NOW}})
    assert "0 分钟前" not in html and "em 还没有数据：ssh: connect failed" in html


def test_stale_card_without_age_does_not_say_zero_minutes():
    s = sess("w", "waiting", host="em")
    s.pop("age", None)
    html = web._stale_host(web._wait_card(s, NOW), s)
    assert "0 分钟前" not in html and "—" in html


def test_terminal_stale_speaker_without_age_shows_dash():
    b = {"waiting": [], "busy": [], "idle": [],
         "stale_host_sessions": [dict(sess("w", "waiting"), host="em")]}
    _, stale = ui._speakers(b)
    s = stale[("em", P)]
    assert s["age"] is None
    row = dict(ui._minimal_row("em", P, s, NOW))
    line = ui.strip_ansi(ui.project_line(row, NOW, live=s))
    assert "<1m 前" not in line and "live 数据 — 前" not in line and "live 数据 —" in line


def test_minimal_row_without_times_shows_dash():
    s = sess("w", "waiting", started_at=None, idle_since=None, segments=[])
    r = ui._minimal_row("s-host", P, s, NOW)
    assert r["activity"] is None
    line = ui.strip_ansi(ui.project_line(r, NOW, live=s))
    assert "<1m" not in line
    assert "—" in line.split("\t")[2]
    assert "last activity —" in ui.strip_ansi(ui.preview_text(r, NOW, s))


# ---- statusline-waiting.sh: both sides of the 120 s window

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def run_segment(cache, updated):
    live_dir = cache / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / "statusline.state").write_text(json.dumps(
        {"updated": updated, "waiting": [{"project": "p", "session_id": "x"}]}))
    env = {"PATH": os.environ["PATH"], "HOME": str(cache), "PROJ_CACHE": str(cache)}
    code = '. "%s"; proj_waiting_segment me' % SCRIPT
    return subprocess.run(["bash", "-c", code], env=env, capture_output=True, text=True).stdout


@needs_jq
def test_statusline_hides_updated_far_in_the_future(tmp_path):
    assert run_segment(tmp_path, time.time() + 100000) == ""
    assert run_segment(tmp_path, time.time() + 1000) == ""


@needs_jq
def test_statusline_shows_updated_slightly_ahead(tmp_path):
    assert "等你 1" in run_segment(tmp_path, time.time() + 30)


@needs_jq
def test_statusline_hides_null_updated(tmp_path):
    assert run_segment(tmp_path, None) == ""
