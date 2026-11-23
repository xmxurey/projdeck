"""Independent spec tests for T03: terminal dark colour constants (spark, task bar, header).
Expected values are literals from the brief; glyph choice (T04) is deliberately not asserted."""
import calendar
import re
import time

import pytest

from projdeck import board as board_mod
from projdeck import ui

DAY0 = calendar.timegm((2026, 11, 18, 0, 0, 0))
NOW = DAY0 + 23 * 3600
CFG = {"hosts": [{"name": "loc", "local": True}, {"name": "s-host", "ssh": "s-host"}]}
SGR = re.compile(r"\x1b\[([0-9;]*)m")
FG = re.compile(r"38;2;(\d+);(\d+);(\d+)")

BUSY, SUB, WAIT, GREY_EMPTY = (57, 135, 229), (25, 158, 112), (217, 89, 38), (42, 47, 55)
DONE, DOING, STUCK, NOTSTARTED = (12, 163, 12), (57, 135, 229), (230, 103, 103), (128, 128, 128)


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def fg_per_char(raw):
    """[(char, fg rgb in force)] for every visible char of a raw ANSI string."""
    out, cur, pos = [], None, 0
    for m in SGR.finditer(raw):
        for ch in raw[pos:m.start()]:
            out.append((ch, cur))
        mm = FG.search(m.group(1))
        if mm:
            cur = tuple(int(x) for x in mm.groups())
        elif m.group(1) in ("", "0"):
            cur = None
        pos = m.end()
    for ch in raw[pos:]:
        out.append((ch, cur))
    return out


def color_at(raw, needle):
    plain = ui.strip_ansi(raw)
    i = plain.index(needle)
    return fg_per_char(raw)[i][1]


def sess(i, state, project, **kw):
    d = {"id": i, "project": project, "cwd": project, "latest_cwd": project, "kind": "interactive",
         "live": True, "name": "n-" + i, "title": "", "started_at": NOW - 1000,
         "status": "idle", "state": state, "idle_since": NOW - 600, "question": "",
         "last_reply": "", "last_human": {"ts": 0, "text": ""}, "last_input": "human",
         "subagents_running": 0, "segments": [], "attach": "", "tasks": None,
         "tasks_owner": None, "errors": []}
    d.update(kw)
    return d


def header_board():
    sessions = [sess("a", "waiting", "/home/u/a"), sess("b", "busy", "/home/u/b", status="busy")]
    lives = [{"schema": 1, "host": "s-host", "collected_at": NOW - 30, "source": "claude agents",
              "since": NOW - 86400, "sessions": sessions, "projects": {}}]
    caches = [{"schema": 1, "host": h, "collected_at": NOW - 60, "synced_at": NOW - 60,
               "stale_since": None, "last_error": None, "projects": []} for h in ("loc", "s-host")]
    return board_mod.build(caches, lives, CFG, NOW)


def bar_colors(items, n):
    """fg colours of the first n visible chars of the task bar (short form)."""
    return [c for _, c in fg_per_char(ui.task_bar(items))[:n]]


# ---- AC1: spark
def test_spark_colors_in_order():
    out = ui.spark(["busy", "sub", "wait", "wait_now", None])
    cells = [c for ch, c in fg_per_char(out) if ch.strip()]
    assert cells == [BUSY, SUB, WAIT, WAIT, GREY_EMPTY]


def test_spark_wait_and_wait_now_same_color_constants():
    assert ui.CELL_RGB["wait"] == (217, 89, 38) == ui.CELL_RGB["wait_now"]
    assert ui.CELL_RGB["busy"] == BUSY and ui.CELL_RGB["sub"] == SUB
    assert ui.WAIT_RGB == (217, 89, 38)


# the terminal timeline / task colours before D6 (CELL_RGB values, WAIT/DOING/DONE/STUCK_RGB)
OLD_RGB = {(111, 155, 203), (169, 198, 228), (240, 163, 94), (201, 132, 70),
           (141, 180, 220), (47, 125, 91), (192, 57, 43)}


def test_spark_old_colors_gone():
    out = ui.spark(["busy", "sub", "wait", "wait_now"])
    cols = {c for ch, c in fg_per_char(out) if ch.strip()}
    assert cols == {BUSY, SUB, WAIT}
    consts = set(ui.CELL_RGB.values()) | {ui.WAIT_RGB, ui.DOING_RGB, ui.DONE_RGB, ui.STUCK_RGB}
    assert not consts & OLD_RGB, consts & OLD_RGB
    shown = "".join([out, "".join(ui.header_lines(header_board(), NOW)),
                     ui.task_bar(_items(["done", "doing", "stuck", "todo"]))])
    assert not {c for _, c in fg_per_char(shown)} & OLD_RGB


# ---- AC2: task bar
def _items(statuses):
    return [{"id": "T%02d" % i, "title": "t", "status": s} for i, s in enumerate(statuses, 1)]


def test_task_bar_done_doing_stuck_colors():
    cols = bar_colors(_items(["done", "doing", "stuck"]), 3)
    assert cols == [DONE, DOING, STUCK]


def test_task_bar_not_started_is_grey():
    cols = bar_colors(_items(["done", "doing", "todo", "todo"]), 4)
    assert cols == [DONE, DOING, NOTSTARTED, NOTSTARTED]


def test_task_bar_constants():
    assert ui.DONE_RGB == (12, 163, 12)
    assert ui.DOING_RGB == (57, 135, 229)
    assert ui.STUCK_RGB == (230, 103, 103)


def test_task_bar_long_form_marker_colors():
    # over 16 tasks: long form "n/m ▣T13"; the current-task marker keeps the doing colour
    st = ["done"] * 12 + ["doing"] + ["todo"] * 8
    out = ui.task_bar(_items(st))
    assert "12/21" in ui.strip_ansi(out)
    marker = ui.strip_ansi(out).index("T13") - 1
    assert fg_per_char(out)[marker][1] == DOING


# ---- AC3: header
def test_header_marker_colors():
    h = ui.header_lines(header_board(), NOW)
    assert color_at(h[0], "◆") == WAIT
    assert color_at(h[0], "◉") == BUSY


def test_header_legend_swatches_match_cell_rgb():
    h = ui.header_lines(header_board(), NOW)
    cols = [c for ch, c in fg_per_char(h[1]) if ch in "▆▒█▇■"]
    assert cols[:3] == [ui.CELL_RGB["busy"], ui.CELL_RGB["sub"], ui.CELL_RGB["wait"]]
    assert cols[:3] == [BUSY, SUB, WAIT]


# ---- AC4: unchanged constants
def test_unchanged_constants():
    assert tuple(ui.EMPTY_CELL) == GREY_EMPTY
    assert tuple(ui.GREY_RGB) == NOTSTARTED


def test_tag_palette_unchanged():
    assert [tuple(c) for c in ui.TAG_PALETTE] == [
        (141, 164, 191), (147, 174, 143), (196, 169, 126),
        (181, 149, 174), (127, 174, 170), (190, 145, 132)]
