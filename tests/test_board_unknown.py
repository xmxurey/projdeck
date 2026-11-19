"""F3: the board says per host whether its live data was checked; unknown is never shown as fact."""
from projdeck import board, ui, web

NOW = 1_800_000_000
P = "/home/u/alpha"


def sess(sid, state, **kw):
    s = {"id": sid, "project": P, "kind": "interactive", "live": state != "ended",
         "name": sid, "title": sid, "started_at": NOW - 5000, "status": "idle", "state": state,
         "idle_since": NOW - 1000, "question": "", "subagents_running": 0,
         "segments": [["busy", NOW - 3000, NOW - 2000]], "attach": "", "errors": []}
    s.update(kw)
    return s


def live(host, sessions, at=NOW - 10, **kw):
    d = {"schema": 1, "host": host, "collected_at": at, "source": "claude agents",
         "sessions": sessions, "projects": {}, "errors": []}
    d.update(kw)
    return d


def project(path=P, **kw):
    p = {"path": path, "exists": True, "last_mtime": NOW - 100, "sessions": [],
         "git": {"branch": "main", "dirty": 0, "ahead": 0, "no_upstream": False,
                 "last_commit_ts": NOW - 3600, "last_commit_subject": "s",
                 "commit_ts_14d": [NOW - 3600]}, "errors": []}
    p.update(kw)
    return p


def cache(host, projects=(), at=NOW - 100):
    return {"schema": 1, "host": host, "collected_at": at, "synced_at": at, "stale_since": None,
            "last_error": None, "projects": list(projects)}


CFG = {"hosts": [{"name": "s-host", "local": True, "mux": "zellij"},
                 {"name": "em", "ssh": "em"}],
       "tiers_days": {"active": 14, "stale": 90}}


def ids(xs):
    return [x["id"] for x in xs]


def hosts(b):
    return {h["name"]: h for h in b["hosts"]}


# ---- hosts[]: errors, live_unknown, zellij_checked

def test_hosts_carry_live_errors_and_unknown_flag():
    lv = live("s-host", [], errors=["/home/u/alpha: git: boom"])
    b = board.build([cache("s-host")], [lv], CFG, NOW)
    h = hosts(b)
    assert h["s-host"]["errors"] == ["/home/u/alpha: git: boom"]
    assert h["s-host"]["live_unknown"] is False
    assert h["em"]["errors"] == [] and h["em"]["live_unknown"] is True  # never collected


def test_source_none_makes_the_host_unknown():
    b = board.build([], [live("s-host", [], source="none", errors=["live agents: x"])], CFG, NOW)
    assert hosts(b)["s-host"]["live_unknown"] is True


def test_a_git_error_alone_does_not_hide_live_sessions():
    lv = live("s-host", [sess("w", "waiting")], errors=["/home/u/beta: git: boom"])
    b = board.build([], [lv], CFG, NOW)
    assert ids(b["waiting"]) == ["w"]
    assert board.statusline_doc(b, NOW)["waiting"][0]["session_id"] == "w"


def test_zellij_checked_only_for_in_scope_hosts_with_data():
    lives = [live("s-host", [], zellij_exited=[]), live("em", [], zellij_exited=["x"])]
    h = hosts(board.build([], lives, CFG, NOW))
    assert h["s-host"]["zellij_checked"] is True
    assert h["em"]["zellij_checked"] is False  # remote: zellij is never collected there
    h = hosts(board.build([], [live("s-host", [], zellij_exited=None)], CFG, NOW))
    assert h["s-host"]["zellij_checked"] is False


def test_exited_count_only_from_checked_hosts():
    lives = [live("s-host", [], zellij_exited=["a"]), live("em", [], zellij_exited=["b", "c"])]
    s = board.build([], lives, CFG, NOW)["summary"]
    assert s["zellij_exited_n"] == 1
    assert [e["name"] for e in s["zellij_exited"]] == ["a"]


# ---- sessions of an unknown host

def unknown_board():
    lv = live("s-host", [sess("e1", "ended", segments=[["busy", NOW - 600, NOW - 300]]),
                     sess("e2", "ended")],
              source="none", errors=["live agents: claude agents failed"])
    return board.build([cache("s-host", [project()])], [lv], CFG, NOW)


def test_unknown_host_sessions_go_to_unknown_not_ended_today():
    b = unknown_board()
    assert ids(b["unknown"]) == ["e1", "e2"]
    assert b["ended_today"] == [] and b["waiting"] == [] and b["busy"] == []
    assert {s["host"] for s in b["unknown"]} == {"s-host"}
    assert board.statusline_doc(b, NOW)["waiting"] == []


def test_unknown_sessions_are_lanes_with_unknown_state():
    b = unknown_board()
    assert {r["state"] for r in b["lanes"]["rows"]} == {"unknown"}


def test_tile_of_unknown_host_says_unknown():
    tile = unknown_board()["tiles"][0]
    assert tile["state"] == "unknown"


def test_tile_of_stale_remote_host_says_unknown():
    lv = live("em", [sess("w", "waiting")], at=NOW - 1000)
    b = board.build([cache("em", [project()])], [lv], CFG, NOW)
    assert b["stale_host_sessions"] and b["tiles"][0]["state"] == "unknown"


def test_tile_of_fresh_host_without_sessions_says_none():
    b = board.build([cache("s-host", [project()])], [live("s-host", [])], CFG, NOW)
    assert b["tiles"][0]["state"] == "none"


# ---- git failures are not "no commits"

def test_lane_marks_live_git_failure():
    lv = live("s-host", [sess("b", "busy", status="busy")],
              projects={P: {"last_commit": None, "commits": None, "error": "git: boom"}})
    row = board.build([], [lv], CFG, NOW)["lanes"]["rows"][0]
    assert row["git_failed"] is True and row["commits"] == []


def test_lane_marks_collect_git_failure_when_live_has_no_entry():
    c = cache("s-host", [project(git=None, errors=["git: timed out"])])
    row = board.build([c], [live("s-host", [sess("b", "busy", status="busy")])], CFG, NOW)["lanes"]["rows"][0]
    assert row["git_failed"] is True


def test_lane_without_git_failure_is_not_marked():
    lv = live("s-host", [sess("b", "busy", status="busy")],
              projects={P: {"last_commit": None, "commits": []}})
    row = board.build([cache("s-host", [project()])], [lv], CFG, NOW)["lanes"]["rows"][0]
    assert row["git_failed"] is False


def test_ended_card_commit_count_unknown_on_git_failure():
    lv = live("s-host", [sess("e", "ended", segments=[["busy", NOW - 600, NOW - 300]])],
              projects={P: {"last_commit": None, "commits": None, "error": "git: boom"}})
    e = board.build([], [lv], CFG, NOW)["ended_today"][0]
    assert e["commits_today"] is None


# ---- web and terminal show it

def test_web_header_names_unavailable_source_and_errors():
    b = unknown_board()
    html = web.render(b, "home", fragment=True)
    assert "活会话来源不可用" in html
    assert "claude agents failed" in html
    assert "状态未知" in html  # the unknown sessions are listed as such


def test_web_header_lists_live_errors():
    lv = live("s-host", [], errors=["/home/u/alpha: git: boom"])
    html = web.render(board.build([], [lv], CFG, NOW), "home", fragment=True)
    assert "git: boom" in html


def test_web_lane_says_git_failed():
    lv = live("s-host", [sess("b", "busy", status="busy")],
              projects={P: {"last_commit": None, "commits": None, "error": "git: boom"}})
    html = web.render_timeline(board.build([], [lv], CFG, NOW))
    assert "git 读取失败" in html


def test_web_ended_card_does_not_claim_zero_commits_on_git_failure():
    lv = live("s-host", [sess("e", "ended", segments=[["busy", NOW - 600, NOW - 300]])],
              projects={P: {"last_commit": None, "commits": None, "error": "git: boom"}})
    html = web.render_columns(board.build([], [lv], CFG, NOW))
    assert "提交 0 次" not in html and "提交 — 次" not in html


def test_web_tile_shows_unknown_state():
    html = web.render(unknown_board(), "projects", fragment=True)
    assert "状态未知" in html and "没有会话在跑" not in html


def test_terminal_header_names_unknown_and_errors():
    first = ui.strip_ansi(ui.header_lines(unknown_board(), NOW)[0])
    assert "活会话来源不可用" in first
    lv = live("s-host", [], errors=["/home/u/alpha: git: boom"])
    first = ui.strip_ansi(ui.header_lines(board.build([], [lv], CFG, NOW), NOW)[0])
    assert "git: boom" in first


def test_terminal_header_exited_count_only_checked_hosts():
    lives = [live("s-host", [], zellij_exited=None), live("em", [], zellij_exited=["b"])]
    first = ui.strip_ansi(ui.header_lines(board.build([], lives, CFG, NOW), NOW)[0])
    assert "已退出 zellij" not in first


def test_minimal_row_does_not_claim_non_git():
    lv = live("s-host", [sess("w", "waiting", project="/home/u/only-live")])
    b = board.build([], [lv], CFG, NOW)
    lines = [ui.strip_ansi(x) for x in ui.project_lines([], NOW, b)]
    row = [x for x in lines if "only-live" in x]
    assert row and "non-git" not in row[0]
