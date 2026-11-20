import re
import time

from projdeck import ui
from projdeck.ui import strip_ansi

NOW = 100 * 86400 + 12 * 3600


def row(path="/home/u/quant", host="s-host", **kw):
    r = {"host": host, "path": path, "exists": True, "local": False, "ssh": host, "home": "/home/u",
         "tier": "active", "activity": NOW - 3600, "stale_since": None, "last_mtime": 0, "git": None,
         "suspicious": [], "progress": "", "errors": [], "sessions": [], "tag": None, "flag": False}
    r.update(kw)
    return r


def sess(sid, state, project="/home/u/quant", host="s-host", **kw):
    d = {"id": sid, "host": host, "project": project, "state": state, "name": "n-" + sid,
         "title": "title " + sid, "started_at": NOW - 5000, "idle_since": NOW - 600,
         "segments": [], "tasks": None, "tasks_owner": None, "question": ""}
    d.update(kw)
    return d


def board(waiting=(), busy=(), idle=(), stale_host=(), hosts=None, **summary):
    s = {"waiting": len(waiting), "busy": len(busy), "idle": len(idle), "open_todos": 4,
         "today": {"busy": 2 * 3600 + 58 * 60, "sub": 3 * 3600 + 11 * 60, "wait": 7 * 3600 + 27 * 60}}
    s.update(summary)
    return {"hosts": hosts or [{"name": "s-host", "live_at": NOW - 60, "live_synced_at": NOW - 60}], "summary": s,
            "waiting": list(waiting), "busy": list(busy), "idle": list(idle),
            "stale_host_sessions": list(stale_host), "ended_today": [], "stale": []}


def items(n, done, doing=None):
    out = []
    for i in range(1, n + 1):
        st = "done" if i <= done else "todo"
        if i == doing:
            st = "doing"
        out.append({"id": "T%02d" % i, "title": "t", "status": st})
    return out


def texts(lines):
    return [strip_ansi(ln.split("\t", 2)[2]) for ln in lines]


def test_waiting_group_marker_tail_and_no_duplicates():
    rows = [row("/home/u/quant", flag=True), row("/home/u/other")]
    b = board(waiting=[sess("w", "waiting", question="Delete\nthe  table?")])
    lines = ui.project_lines(rows, NOW, b)
    t = texts(lines)
    assert t[0].startswith("── ◆ 等你 ──")
    assert t[1].lstrip().startswith("◆") and t[1].endswith("Delete the table?")
    assert sum("quant" in x for x in t) == 1
    assert not any("🚩" in x for x in t)
    assert any(x.startswith("── s-host") for x in t)


def test_idle_last_in_progress_group_and_marker():
    rows = [row("/home/u/a"), row("/home/u/b")]
    b = board(idle=[sess("i", "idle", "/home/u/a", idle_since=NOW - 125)],
              busy=[sess("b", "busy", "/home/u/b")])
    t = texts(ui.project_lines(rows, NOW, b))
    assert t[0].startswith("── ◉ 进行中 ──")
    assert "b" in t[1] and t[1].lstrip().startswith("◉")
    assert t[2].lstrip().startswith("◌") and "空闲 2m" in t[2]


def test_every_row_has_24_cells_with_palette():
    segs = [["busy", NOW - 10800, NOW - 9000], ["wait", NOW - 3000, NOW]]
    rows = [row("/home/u/a"), row("/home/u/b")]
    b = board(busy=[sess("b", "busy", "/home/u/a", segments=segs)])
    for ln in ui.project_lines(rows, NOW, b):
        host, path, disp = ln.split("\t", 2)
        if path:
            assert strip_ansi(disp).count("▆") == 24
    disp = ui.project_lines(rows, NOW, b)[1].split("\t", 2)[2]
    assert "\x1b[38;2;111;155;203m▆" in disp and "\x1b[38;2;240;163;94m▆" in disp
    assert "\x1b[38;2;42;47;55m▆" in disp


def test_spark_colors():
    s = ui.spark(["busy", "sub", "wait", "wait_now", None])
    assert strip_ansi(s) == "▆" * 5
    for rgb in ("111;155;203", "169;198;228", "240;163;94", "201;132;70", "42;47;55"):
        assert "\x1b[38;2;%sm▆" % rgb in s


def test_task_bar_short_and_long():
    assert strip_ansi(ui.task_bar({"current": "T07", "items": items(8, 6, 7)})) == "■■■■■■▣□ 6/8 T07"
    assert strip_ansi(ui.task_bar(items(8, 6, 7))) == "■■■■■■▣□ 6/8 T07"
    assert strip_ansi(ui.task_bar(items(21, 12, 13))) == "12/21 ▣T13"
    assert ui.task_bar({"items": []}) == "" and ui.task_bar(None) == ""
    stuck = items(3, 1) 
    stuck[1]["status"] = "stuck"
    assert strip_ansi(ui.task_bar(stuck)) == "■▣□ 1/3"


def test_busy_tail_task_bar_title_and_todos():
    rows = [row("/home/u/a"), row("/home/u/b", backlog={"open": 3, "done": 1, "items": []}),
            row("/home/u/c")]
    b = board(busy=[sess("a", "busy", "/home/u/a", tasks={"items": items(8, 6, 7), "current": "T07"}),
                    sess("c", "sub", "/home/u/c", title="fix rebate")])
    t = texts(ui.project_lines(rows, NOW, b))
    assert any(x.endswith("■■■■■■▣□ 6/8 T07") for x in t)
    assert any(x.endswith("fix rebate") for x in t)
    assert any(x.endswith("☐3") and x.lstrip().split()[1] == "b" for x in t)


def test_header_lines():
    b = board(waiting=[sess("w", "waiting")], idle=[sess("i", "idle")],
              hosts=[{"name": "s-host", "live_at": NOW - 60, "live_synced_at": NOW - 60}, {"name": "em", "live_at": NOW - 400, "live_synced_at": NOW - 400}])
    l1, l2 = ui.header_lines(b, NOW)
    p1 = strip_ansi(l1)
    hhmm = time.strftime("%H:%M", time.localtime(NOW - 60))
    assert "s-host 数据 " + hhmm in p1 and "实时" not in p1
    assert "^U 刷新" in p1 and p1.count("^U 刷新") == 1
    assert "\x1b[33mem 数据" in l1 and "\x1b[33msg" not in l1
    assert re.search(r"◆ 等你 1  ◉ 进行中 0  ◌ 空闲 1  ☐ 待办 4", p1)
    p2 = strip_ansi(l2)
    assert p2.startswith("今天") and "Claude 2h58m" in p2 and "子代理 3h11m" in p2
    assert "闲置等输入 7h27m" in p2


def test_header_fresh_hosts_not_yellow():
    l1, _ = ui.header_lines(board(), NOW)
    assert "\x1b[33m" not in l1 and "^U 刷新" not in l1


def test_stale_host_session_stays_in_host_group_with_warning():
    rows = [row("/home/u/a")]
    b = board(stale_host=[sess("w", "waiting", "/home/u/a", age=900)])
    t = texts(ui.project_lines(rows, NOW, b))
    assert not any("等你" in x for x in t if x.startswith("──"))
    assert any("⚠ live 数据 15m 前" in x for x in t)


def test_unknown_live_project_gets_minimal_row():
    b = board(busy=[sess("b", "busy", "/home/u/newproj")])
    t = texts(ui.project_lines([], NOW, b))
    assert any("newproj" in x for x in t)


def test_board_none_keeps_the_old_line_format():
    rows = [row("/home/u/a", flag=True), row("/home/u/b")]
    assert texts(ui.project_lines(rows, NOW)) == [
        "── 🚩 优先 " + "─" * 36,
        "● a" + " " * 31 + "1h  @s-host non-git",
        "── s-host " + "─" * 34,
        "● b" + " " * 31 + "1h  non-git"]


def test_minimal_row_uses_session_time_and_keeps_host_group_together():
    rows = [row("/home/u/a", host="s-host"), row("/home/u/b", host="em")]
    old = sess("n", "busy", "/home/u/new", host="s-host", age=900, idle_since=NOW - 7200, started_at=NOW - 9000,
               segments=[["busy", NOW - 9000, NOW - 7200]])
    t = texts(ui.project_lines(rows, NOW, board(stale_host=[old])))
    assert sum(x.startswith("── s-host") for x in t) == 1
    i = next(i for i, x in enumerate(t) if "new" in x)
    assert t[i - 1].split()[1:2] == ["a"]
    assert "<1m" not in t[i] and " 2h " in t[i]


def test_two_old_hosts_write_refresh_hint_once():
    b = board(hosts=[{"name": "s-host", "live_at": NOW - 400, "live_synced_at": NOW - 400}, {"name": "em", "live_at": NOW - 500, "live_synced_at": NOW - 500}])
    assert strip_ansi(ui.header_lines(b, NOW)[0]).count("^U 刷新") == 1


def test_multi_session_project_goes_to_most_urgent_group():
    rows = [row("/home/u/a")]
    b = board(waiting=[sess("w", "waiting", "/home/u/a", question="q?")],
              busy=[sess("b", "busy", "/home/u/a")])
    t = texts(ui.project_lines(rows, NOW, b))
    assert sum(x.lstrip().split()[1:2] == ["a"] for x in t) == 1
    assert not any(x.startswith("── ◉") for x in t)
