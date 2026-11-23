"""Independent spec tests for T26: terminal rows, groups and header (ui.project_line /
project_lines / header_lines / task_bar / spark / pick_project(board=...))."""
import calendar
import json
import os
import re
import time

import pytest

from projdeck import board as board_mod
from projdeck import ui

DAY0 = calendar.timegm((2026, 11, 18, 0, 0, 0))
NOW = DAY0 + 23 * 3600
CFG = {"hosts": [{"name": "loc", "local": True}, {"name": "s-host", "ssh": "s-host"},
                 {"name": "em", "ssh": "em"}]}

ORANGE, BLUE = (217, 89, 38), (57, 135, 229)
C_BUSY, C_SUB, C_WAIT, C_NOW, C_EMPTY = ((57, 135, 229), (25, 158, 112), (217, 89, 38),
                                         (217, 89, 38), (42, 47, 55))
KIND_COLOR = {"busy": C_BUSY, "sub": C_SUB, "wait": C_WAIT, "wait_now": C_NOW, None: C_EMPTY}

SGR = re.compile(r"\x1b\[([0-9;]*)m")


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def plain(s):
    return ui.strip_ansi(s)


def row(host="s-host", path="/srv/p1", **kw):
    r = {"host": host, "path": path, "exists": True, "local": host == "loc", "ssh": host,
         "tier": "active", "activity": NOW - 3600, "stale_since": None, "last_mtime": 0,
         "git": None, "suspicious": [], "progress": "", "errors": [], "sessions": [],
         "tag": None, "flag": False, "tags": []}
    r.update(kw)
    return r


def sess(i, state, project="/srv/p1", **kw):
    d = {"id": i, "project": project, "cwd": project, "latest_cwd": project, "kind": "interactive",
         "live": True, "name": "n-" + i, "title": "", "started_at": NOW - 1000,
         "status": "idle", "state": state, "idle_since": NOW - 600, "question": "",
         "last_reply": "", "last_human": {"ts": 0, "text": ""}, "last_input": "human",
         "subagents_running": 0, "segments": [], "attach": "", "tasks": None,
         "tasks_owner": None, "errors": []}
    d.update(kw)
    return d


def live(host, sessions, at):
    return {"schema": 1, "host": host, "collected_at": at, "source": "claude agents",
            "since": at - 86400, "sessions": sessions, "projects": {}}


def cache(host, projects=None):
    return {"schema": 1, "host": host, "collected_at": NOW - 60, "synced_at": NOW - 60,
            "stale_since": None, "last_error": None, "projects": projects or []}


def mkboard(lives, now=NOW, caches=None):
    return board_mod.build(caches or [cache("loc"), cache("s-host"), cache("em")], lives, CFG, now)


def sg_board(sessions, at=None, now=NOW):
    return mkboard([live("s-host", sessions, now - 30 if at is None else at)], now)


def lines_of(rows, board, now=NOW):
    return [ln.split("\t") for ln in ui.project_lines(rows, now, board=board)]


def find(lines, path):
    return [i for i, (_, p, _) in enumerate(lines) if p == path]


def hdr(lines, text):
    hits = [i for i, (_, p, d) in enumerate(lines) if text in plain(d) and p == ""]
    assert len(hits) == 1, (text, [plain(d) for _, _, d in lines])
    return hits[0]


def colors_of_blocks(raw):
    """Foreground rgb in force at each ▆ or ▒ of the raw string."""
    out, cur = [], None
    pos = 0
    for m in re.finditer(r"\x1b\[([0-9;]*)m|[▆▒]", raw):
        if m.group(0) in ("▆", "▒"):
            out.append(cur)
        else:
            mm = re.search(r"38;2;(\d+);(\d+);(\d+)", m.group(1))
            if mm:
                cur = tuple(int(x) for x in mm.groups())
            elif m.group(1) in ("", "0"):
                cur = None
    return out


def color_before(raw, glyph):
    i = raw.index(glyph)
    cur = None
    for m in SGR.finditer(raw[:i]):
        mm = re.search(r"38;2;(\d+);(\d+);(\d+)", m.group(1))
        if mm:
            cur = tuple(int(x) for x in mm.groups())
        elif m.group(1) in ("", "0"):
            cur = None
    return cur


# ---------------------------------------------------------------- groups (AC1)

def test_waiting_project_in_waiting_group_only():
    rows = [row(path="/srv/w", flag=True), row(path="/srv/other"), row(path="/srv/f", flag=True)]
    b = sg_board([sess("a", "waiting", "/srv/w", question="Which db?\nPostgres or SQLite?")])
    lines = lines_of(rows, b)
    wi, fi = hdr(lines, "── ◆ 等你 ──"), hdr(lines, "🚩")
    idx = find(lines, "/srv/w")
    assert len(idx) == 1 and wi < idx[0] < fi  # once, in the waiting group, not in 🚩 / host group
    d = plain(lines[idx[0]][2])
    assert d.startswith("◆")
    assert "Which db?" in d and "Postgres or SQLite?" in d
    assert "\n" not in lines[idx[0]][2]
    assert "@s-host" in d  # host named in the cross-host group
    assert color_before(lines[idx[0]][2], "◆") == ORANGE
    assert find(lines, "/srv/f")[0] > fi


def test_groups_come_before_flag_group_and_host_groups():
    rows = [row(path="/srv/w"), row(path="/srv/b"), row(path="/srv/f", flag=True),
            row(path="/srv/n")]
    b = sg_board([sess("a", "waiting", "/srv/w"), sess("b", "busy", "/srv/b", status="busy")])
    lines = lines_of(rows, b)
    wi, bi, fi, hi = (hdr(lines, "── ◆ 等你 ──"), hdr(lines, "── ◉ 进行中 ──"), hdr(lines, "🚩"),
                      hdr(lines, "── s-host"))
    assert wi < bi < fi < hi
    assert wi < find(lines, "/srv/w")[0] < bi < find(lines, "/srv/b")[0] < fi
    assert find(lines, "/srv/f")[0] > fi and find(lines, "/srv/n")[0] > hi
    d = plain(lines[find(lines, "/srv/b")[0]][2])
    assert d.startswith("◉")
    assert color_before(lines[find(lines, "/srv/b")[0]][2], "◉") == BLUE


def test_in_progress_group_lists_sub_busy_before_idle_with_marker():
    rows = [row(path="/srv/i"), row(path="/srv/b"), row(path="/srv/s")]
    b = sg_board([sess("i", "idle", "/srv/i", idle_since=NOW - 600),
                  sess("b", "busy", "/srv/b", status="busy"),
                  sess("s", "sub", "/srv/s", status="busy")])
    lines = lines_of(rows, b)
    ii, bi, si = (find(lines, p)[0] for p in ("/srv/i", "/srv/b", "/srv/s"))
    assert max(bi, si) < ii
    assert ii > hdr(lines, "── ◉ 进行中 ──")
    d = plain(lines[ii][2])
    assert d.startswith("◌") and "空闲 10m" in d
    assert plain(lines[bi][2]).startswith("◉") and plain(lines[si][2]).startswith("◉")


def test_multi_session_project_takes_most_urgent():
    q = [["w", 0, 0]]
    rows = [row(path="/srv/m"), row(path="/srv/n")]
    segs_wait = [["wait", NOW - 10800, NOW]]
    segs_busy = [["busy", NOW - 10800, NOW]]
    b = sg_board([sess("i", "idle", "/srv/m"), sess("b", "busy", "/srv/m", status="busy",
                                                      segments=segs_busy),
                  sess("w", "waiting", "/srv/m", question="need you", segments=segs_wait),
                  sess("i2", "idle", "/srv/n"), sess("b2", "busy", "/srv/n", status="busy",
                                                       segments=segs_busy)])
    lines = lines_of(rows, b)
    for p in ("/srv/m", "/srv/n"):
        assert len(find(lines, p)) == 1
    m, n = lines[find(lines, "/srv/m")[0]], lines[find(lines, "/srv/n")[0]]
    wi = hdr(lines, "── ◆ 等你 ──")
    assert find(lines, "/srv/m")[0] > wi and find(lines, "/srv/m")[0] < hdr(lines, "── ◉ 进行中 ──")
    assert "need you" in plain(m[2])
    assert colors_of_blocks(m[2]) == [C_WAIT] * 24  # timeline of the waiting session only
    assert plain(n[2]).startswith("◉")  # busy beats idle
    assert colors_of_blocks(n[2]) == [C_BUSY] * 24


def test_project_only_in_live_still_shown():
    rows = [row(path="/srv/old")]
    b = sg_board([sess("a", "waiting", "/srv/newproj", question="hi")])
    lines = lines_of(rows, b)
    idx = find(lines, "/srv/newproj")
    assert len(idx) == 1 and lines[idx[0]][0] == "s-host"
    assert idx[0] > hdr(lines, "── ◆ 等你 ──")
    assert find(lines, "/srv/old")


def test_ended_or_no_live_session_stays_in_host_group():
    rows = [row(path="/srv/x")]
    b = sg_board([])
    lines = lines_of(rows, b)
    assert not any("── ◆ 等你 ──" in plain(d) or "── ◉ 进行中 ──" in plain(d) for _, _, d in lines)
    assert lines[0][:2] == ["s-host", ""] and lines[1][1] == "/srv/x"


# ---------------------------------------------------------------- 24 cells (AC2)

def test_every_project_row_has_24_blocks():
    rows = [row(path="/srv/w"), row(path="/srv/b"), row(path="/srv/n"), row(path="/srv/f", flag=True)]
    b = sg_board([sess("a", "waiting", "/srv/w"), sess("b", "busy", "/srv/b", status="busy")])
    for h, p, d in lines_of(rows, b):
        if p:
            assert plain(d).count("▆") == 24, p
            assert len(colors_of_blocks(d)) == 24
        else:
            assert plain(d).count("▆") == 0


def test_no_session_row_has_24_empty_cells():
    raw = ui.project_line(row(), NOW, live={}).split("\t")[2]
    assert plain(raw).count("▆") == 24
    assert colors_of_blocks(raw) == [C_EMPTY] * 24


def test_cells_colors_match_mockup_fixture():
    fx = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "mockup_terminal.json")))
    now = fx["now"] * 60
    for key, state in (("qp", "waiting"), ("pc", "busy")):
        segs = [[{"b": "busy", "s": "sub", "w": "wait", "live": "wait_now"}[k], a * 60, e * 60]
                for k, a, e in fx["segs"][key]]
        s = sess(key, state, "/srv/p1", segments=segs, status="busy" if state == "busy" else "idle",
                 question="q")
        raw = ui.project_line(row(), now, live=s).split("\t")[2]
        exp = [KIND_COLOR[fx["map"][k]] for k in fx["expected"][key + "_24"]]
        assert colors_of_blocks(raw) == exp, key


def test_spark_renders_cells():
    out = ui.spark(["busy", None, "sub", "wait", "wait_now"])
    assert plain(out) == "▆▆▆▆▒"
    assert colors_of_blocks(out) == [C_BUSY, C_EMPTY, C_SUB, C_WAIT, C_NOW]
    assert plain(ui.spark([])) == ""


# ---------------------------------------------------------------- task bar (AC3)

def items(n_done, doing_id, n_todo, n_stuck=0):
    out = [{"id": "T%02d" % i, "title": "t", "status": "done"} for i in range(1, n_done + 1)]
    out.append({"id": doing_id, "title": "t", "status": "doing"})
    out += [{"id": "X%02d" % i, "title": "t", "status": "todo"} for i in range(n_todo)]
    out += [{"id": "S%02d" % i, "title": "t", "status": "stuck"} for i in range(n_stuck)]
    return out


def test_task_bar_segmented():
    assert plain(ui.task_bar(items(6, "T07", 1))) == "■■■■■■▣□ 6/8 T07"


def test_task_bar_long_form_over_16():
    assert plain(ui.task_bar(items(12, "T13", 8))) == "12/21 ▣T13"


def test_task_bar_boundary_16_vs_17():
    sixteen = plain(ui.task_bar(items(5, "T06", 10)))
    assert sixteen.startswith("■■■■■▣" + "□" * 10) and sixteen.endswith("5/16 T06")
    seventeen = plain(ui.task_bar(items(5, "T06", 11)))
    assert seventeen == "5/17 ▣T06"


def test_task_bar_stuck_is_red_bang_and_empty_is_blank():
    out = ui.task_bar(items(1, "T02", 0, n_stuck=1))
    assert plain(out).startswith("■▣!")
    i = out.rindex("!")  # the stuck cell (the doing cell comes before it)
    cur = None
    for m in SGR.finditer(out[:i]):
        mm = re.search(r"38;2;(\d+);(\d+);(\d+)", m.group(1))
        if mm:
            cur = tuple(int(x) for x in mm.groups())
        elif m.group(1) in ("", "0"):
            cur = None
    assert cur == (230, 103, 103)
    assert plain(ui.task_bar([])).strip() == ""
    assert plain(ui.task_bar(None)).strip() == ""


def test_busy_row_tail_task_bar_and_title_fallback():
    rows = [row(path="/srv/t"), row(path="/srv/u")]
    t = {"label": "plan", "items": items(6, "T07", 1)}
    b = sg_board([sess("a", "busy", "/srv/t", status="busy", tasks=t, title="Refactor parser"),
                  sess("b", "busy", "/srv/u", status="busy", title="Fix flaky test")])
    lines = lines_of(rows, b)
    assert "■■■■■■▣□ 6/8 T07" in plain(lines[find(lines, "/srv/t")[0]][2])
    d = plain(lines[find(lines, "/srv/u")[0]][2])
    assert "Fix flaky test" in d and "■" not in d


def test_other_rows_show_open_todos():
    r = row(backlog={"open": 3, "done": 1})
    d = plain(ui.project_line(r, NOW, live={}).split("\t")[2])
    assert "☐3" in d
    assert "☐" not in plain(ui.project_line(row(), NOW, live={}).split("\t")[2])


# ---------------------------------------------------------------- header (AC4)

def two_host_board(sg_at, em_at, extra=()):
    lives = [live("s-host", [sess("a", "waiting", "/srv/a"), sess("b", "waiting", "/srv/b")]
                  + list(extra), sg_at),
             live("em", [sess("c", "busy", "/srv/c", status="busy"),
                         sess("d", "sub", "/srv/d", status="busy"),
                         sess("e", "idle", "/srv/e")], em_at),
             live("loc", [], NOW - 10)]
    return mkboard(lives)


def hhmm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


YELLOW = re.compile(r"\x1b\[(?:[0-9;]*;)?(?:33|93)m|38;5;(?:3|11|172|178|214|220|221|226|227)m"
                    r"|38;2;(?:2[0-5]\d|1[89]\d);(?:1[5-9]\d|2[0-5]\d);(?:[0-9]|[1-9]\d)m")


def test_header_line1_fresh():
    b = two_host_board(NOW - 120, NOW - 200)
    h = ui.header_lines(b, NOW)
    assert isinstance(h, list) and len(h) == 2
    l1 = plain(h[0])
    assert "s-host 数据 " + hhmm(NOW - 120) in l1 and "em 数据 " + hhmm(NOW - 200) in l1
    assert "实时" not in l1
    s = b["summary"]
    assert (s["waiting"], s["busy"], s["idle"]) == (2, 2, 1)
    assert "◆ 等你 2  ◉ 进行中 2  ◌ 空闲 1  ☐ 待办 %d" % s["open_todos"] in l1
    assert "^U 刷新" not in l1
    end = h[0].index(hhmm(NOW - 200)) + 5
    assert not YELLOW.search(h[0][:end])


def test_header_stale_host_is_yellow_with_refresh_hint():
    b = two_host_board(NOW - 400, NOW - 100)
    h = ui.header_lines(b, NOW)
    l1 = plain(h[0])
    assert "s-host 数据 " + hhmm(NOW - 400) in l1 and "^U 刷新" in l1
    end = h[0].index(hhmm(NOW - 400)) + 5
    assert YELLOW.search(h[0][:end]), repr(h[0])


def test_header_missing_live_at_shows_dash():
    b = two_host_board(NOW - 100, NOW - 100)
    for hst in b["hosts"]:
        if hst["name"] == "s-host":
            hst["live_at"] = None
    l1 = plain(ui.header_lines(b, NOW)[0])
    assert "s-host 数据 —" in l1 and "None" not in l1
    assert "em 数据 " + hhmm(NOW - 100) in l1


def test_header_counts_match_summary_and_exclude_stale_host():
    b = two_host_board(NOW - 900, NOW - 100)  # s-host's two waiting sessions are stale-host
    s = b["summary"]
    assert s["waiting"] == 0
    l1 = plain(ui.header_lines(b, NOW)[0])
    assert "◆ 等你 0  ◉ 进行中 2  ◌ 空闲 1" in l1


def test_header_line2_today_totals():
    segs1 = [["busy", DAY0 + 3600, DAY0 + 3600 + 10680]]
    segs2 = [["sub", DAY0 + 3600, DAY0 + 3600 + 11460]]
    segs3 = [["wait", DAY0 + 3600, DAY0 + 3600 + 20000], ["wait_now", NOW - 6820, NOW]]
    b = sg_board([sess("a", "busy", "/srv/a", status="busy", segments=segs1),
                  sess("b", "sub", "/srv/b", status="busy", segments=segs2),
                  sess("c", "idle", "/srv/c", segments=segs3)])
    h = ui.header_lines(b, NOW)
    l2 = plain(h[1])
    assert l2.startswith("今天")
    assert "Claude 2h58m" in l2 and "子代理 3h11m" in l2 and "闲置等输入 7h27m" in l2
    assert l2.index("Claude") < l2.index("子代理") < l2.index("闲置等输入")


def test_header_empty_board_is_all_zero_and_not_none():
    b = mkboard([])
    h = ui.header_lines(b, NOW)
    assert len(h) == 2
    l1 = plain(h[0])
    assert "◆ 等你 0  ◉ 进行中 0  ◌ 空闲 0  ☐ 待办 0" in l1
    assert "None" not in l1 + plain(h[1])


# ---------------------------------------------------------------- stale host (AC5)

def test_stale_host_session_stays_in_host_group_with_age_warning():
    rows = [row(path="/srv/w")]
    b = sg_board([sess("a", "waiting", "/srv/w", question="Q?")], at=NOW - 900)
    lines = lines_of(rows, b)
    assert not any("── ◆ 等你 ──" in plain(d) for _, _, d in lines)
    idx = find(lines, "/srv/w")
    assert len(idx) == 1 and idx[0] > hdr(lines, "── s-host")
    d = plain(lines[idx[0]][2])
    assert "⚠ live 数据 15m 前" in d
    assert not d.startswith("◆")


def test_fresh_host_has_no_warning():
    rows = [row(path="/srv/w")]
    b = sg_board([sess("a", "waiting", "/srv/w")], at=NOW - 300)
    lines = lines_of(rows, b)
    assert all("⚠" not in plain(d) for _, _, d in lines)


def test_local_host_goes_stale_too():
    rows = [row(host="loc", path="/srv/l")]
    b = mkboard([live("loc", [sess("a", "waiting", "/srv/l")], NOW - 5000)])
    lines = lines_of(rows, b)
    assert all("◆ 等你" not in plain(d) for _, _, d in lines)
    idx = find(lines, "/srv/l")  # the project is still listed, with the stale warning
    assert len(idx) == 1 and "⚠ live 数据" in plain(lines[idx[0]][2])


# ---------------------------------------------------------------- board=None unchanged / None handling

def test_board_none_is_old_format():
    rows = [row(path="/a"), row(path="/b", flag=True)]
    lines = ui.project_lines(rows, NOW)
    joined = "".join(lines)
    assert "▆" not in joined and "◆" not in joined and "◉" not in joined
    assert "等你" not in joined and "进行中" not in joined
    assert [ln.split("\t")[:2] for ln in lines] == [["", ""], ["s-host", "/b"], ["s-host", ""], ["s-host", "/a"]]
    assert all("\x1b[" not in ln.split("\t")[2] for ln in lines if ln.split("\t")[1] == "")
    assert "\x1b[" not in ui.project_line(row(), NOW).split("\t")[2]


def test_name_relative_to_home_in_board_mode():
    home = os.path.expanduser("~")
    r = row(path=home + "/dev/projdeck", home=home)
    d = plain(ui.project_line(r, NOW, live={}).split("\t")[2])
    assert "dev/projdeck" in d and home not in d


def test_none_fields_do_not_show_none_or_zero():
    r = row()
    cases = [sess("a", "waiting", question=None, idle_since=None, title=None, tasks=None,
                  segments=None, started_at=None),
             sess("b", "idle", idle_since=None, started_at=None, title=None),
             sess("c", "busy", status="busy", title=None, started_at=None, tasks=None)]
    for s in cases:
        d = plain(ui.project_line(r, NOW, live=s).split("\t")[2])
        assert "None" not in d, s["state"]
        assert plain(d).count("▆") == 24
    d = plain(ui.project_line(r, NOW, live=cases[1]).split("\t")[2])
    assert "空闲 0m" not in d and "空闲 0" not in d


# ---------------------------------------------------------------- pick_project

def test_pick_project_prepends_header_lines_with_board(monkeypatch):
    cap = {}

    def fake(lines, args, expect=()):
        cap["lines"], cap["args"] = lines, args
        return "", [lines[-1]]
    monkeypatch.setattr(ui, "run_fzf", fake)
    rows = [row(path="/srv/w"), row(path="/srv/n")]
    b = sg_board([sess("a", "waiting", "/srv/w", question="Q?")])
    ui.pick_project(rows, NOW, board=b)
    blob = plain("\n".join(cap["args"]))
    h = ui.header_lines(b, NOW)
    assert "--header-first" in cap["args"]
    i1, i2, i3 = blob.index(plain(h[0])), blob.index(plain(h[1])), blob.index("Enter: resume")
    assert i1 < i2 < i3
    assert "^F: search prompts" in blob

    ui.pick_project(rows, NOW)
    blob = plain("\n".join(cap["args"]))
    assert plain(h[1]) not in blob and "Enter: resume" in blob


def test_pick_project_cursor_follows_selected_with_groups(monkeypatch):
    cap = {}

    def fake(lines, args, expect=()):
        cap["lines"], cap["args"] = lines, args
        return "", [lines[-1]]
    monkeypatch.setattr(ui, "run_fzf", fake)
    rows = [row(path="/srv/w"), row(path="/srv/n")]
    b = sg_board([sess("a", "waiting", "/srv/w", question="Q?")])
    res = ui.pick_project(rows, NOW, select=("s-host", "/srv/n"), board=b)
    assert res == ("", "s-host", "/srv/n")
    pos = [i for i, ln in enumerate(cap["lines"]) if ln.split("\t")[:2] == ["s-host", "/srv/n"]][0] + 1
    assert "--bind=load:pos(%d)" % pos in cap["args"]


def test_task_bar_without_doing_item():
    out = plain(ui.task_bar([{"id": "T01", "title": "t", "status": "done"},
                             {"id": "T02", "title": "t", "status": "todo"}]))
    assert out == "■□ 1/2"


def test_long_name_truncated_to_18_columns_in_board_mode():
    name = "abcdefghij" * 3
    d = plain(ui.project_line(row(path="/srv/" + name), NOW, live={}).split("\t")[2])
    assert name not in d and name[:15] in d
    body = d[d.index("abcdefghij"):]
    assert len(body.split()[0]) <= 18


def _minimal_row_lines(host_at, state="waiting", idle_since=NOW - 7200):
    rows = [row(path="/srv/old"), row(path="/srv/other")]
    b = sg_board([sess("a", state, "/srv/new", idle_since=idle_since, started_at=NOW - 9000,
                       question="Q?")], at=host_at)
    return lines_of(rows, b)


def test_minimal_row_on_stale_host_keeps_one_group_and_session_age():
    lines = _minimal_row_lines(NOW - 900)
    assert sum(1 for _, p, d in lines if p == "" and "── s-host" in plain(d)) == 1
    idx = find(lines, "/srv/new")
    assert len(idx) == 1
    d = plain(lines[idx[0]][2])
    assert "<1m" not in d and "2h" in d.split()
    assert any(abs(idx[0] - j) == 1 for j in find(lines, "/srv/old") + find(lines, "/srv/other"))
    assert "⚠ live 数据 15m 前" in d


def test_minimal_row_on_fresh_host_uses_session_time_for_age():
    lines = _minimal_row_lines(NOW - 30)
    idx = find(lines, "/srv/new")
    assert len(idx) == 1
    d = plain(lines[idx[0]][2])
    assert "<1m" not in d and "2h" in d.split()
    assert sum(1 for _, p, dd in lines if p == "" and "── s-host" in plain(dd)) == 1


def test_minimal_row_age_uses_latest_of_segment_end_idle_and_start():
    rows = [row(path="/srv/old")]
    b = sg_board([sess("a", "busy", "/srv/new", status="busy", idle_since=NOW - 7200,
                       started_at=NOW - 9000, segments=[["busy", NOW - 5000, NOW - 3600]])],
                 at=NOW - 30)
    d = plain(lines_of(rows, b)[find(lines_of(rows, b), "/srv/new")[0]][2])
    assert "1h" in d.split()


def test_refresh_hint_once_for_two_stale_hosts_and_once_for_one():
    both = plain(ui.header_lines(two_host_board(NOW - 400, NOW - 500), NOW)[0])
    assert both.count("^U 刷新") == 1
    one = plain(ui.header_lines(two_host_board(NOW - 400, NOW - 100), NOW)[0])
    assert one.count("^U 刷新") == 1
