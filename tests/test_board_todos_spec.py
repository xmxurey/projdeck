"""Spec-derived tests for board top_todos / backlogs / tiles / zellij_exited (T15)."""
import calendar
import time

import pytest

from projdeck import board

NOW = calendar.timegm((2026, 11, 18, 12, 0, 0))
DAY0 = calendar.timegm((2026, 11, 18, 0, 0, 0))
DAY = 86400
CFG = {"hosts": [{"name": "loc", "local": True, "mux": "zellij"}, {"name": "s-host", "ssh": "s-host"}],
       "tiers_days": {"active": 14, "stale": 60}}


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def item(title, done=False, text=None, section=None):
    return {"title": title, "done": done, "section": section, "text": text or title}


def backlog(items, mtime=NOW - 3600):
    n_done = sum(1 for i in items if i["done"])
    return {"path": "docs/backlog.md", "mtime": mtime, "items": items,
            "open": len(items) - n_done, "done": n_done, "raw": None}


def proj(path, flag=False, items=None, mtime=None, git="default", sessions=None, last_mtime=None):
    p = {"path": path, "flag": flag, "exists": True, "sessions": sessions or [],
         "last_mtime": last_mtime if last_mtime is not None else NOW - 100 * DAY,
         "suspicious": [], "errors": []}
    if git == "default":
        git = {"last_commit_ts": NOW - 100 * DAY, "last_commit_subject": "old", "commit_ts_14d": []}
    p["git"] = git
    if items is not None:
        p["backlog"] = backlog(items, mtime if mtime is not None else NOW - 3600)
    return p


def cache(host, projects):
    return {"schema": 1, "host": host, "collected_at": NOW - 60, "synced_at": NOW - 60,
            "stale_since": None, "last_error": None, "projects": projects}


def sess(i, state, project, **kw):
    d = {"id": i, "project": project, "cwd": project, "latest_cwd": project, "kind": "interactive",
         "live": True, "name": "n-" + i, "title": "", "started_at": NOW - 1000, "status": "idle",
         "state": state, "idle_since": NOW - 100, "question": "", "last_reply": "",
         "last_human": {"ts": 0, "text": ""}, "last_input": "human", "subagents_running": 0,
         "segments": [], "attach": "", "tasks": None, "tasks_owner": None, "errors": []}
    d.update(kw)
    return d


def live(host, sessions=(), at=NOW - 10, **kw):
    d = {"schema": 1, "host": host, "collected_at": at, "source": "claude agents",
         "since": at - DAY, "sessions": list(sessions), "projects": {}}
    d.update(kw)
    return d


def build(caches, lives=(), cfg=CFG):
    return board.build(list(caches), list(lives), cfg, NOW)


def titles(b):
    return [t["title"] for t in b["top_todos"]]


# ---------- top_todos (D5) ----------

def test_top_todos_entry_shape():
    c = cache("loc", [proj("/h/a", flag=True, items=[item("do it")])])
    t = build([c])["top_todos"]
    assert len(t) == 1
    for k in ("host", "project", "title", "flag"):
        assert k in t[0]
    assert t[0]["host"] == "loc" and t[0]["project"] == "/h/a" and t[0]["title"] == "do it"


def test_top_todos_priority_first_then_flagged_first_open():
    c = cache("loc", [
        proj("/h/flag", flag=True, items=[item("done one", done=True), item("flag first open"),
                                          item("flag second")]),
        proj("/h/plain", items=[item("plain a"), item("urgent 最优先 thing")]),
    ])
    assert titles(build([c])) == ["urgent 最优先 thing", "flag first open"]


def test_top_todos_priority_matches_in_text_not_only_title():
    c = cache("loc", [proj("/h/p", items=[item("plain"), item("quiet", text="quiet\n  最优先: yes")])])
    assert titles(build([c])) == ["quiet"]


def test_top_todos_done_items_never_appear_even_if_priority_or_flagged_first():
    c = cache("loc", [
        proj("/h/f", flag=True, items=[item("~~最优先 done~~", done=True), item("open one")]),
    ])
    assert titles(build([c])) == ["open one"]


def test_top_todos_flagged_project_all_done_contributes_nothing():
    c = cache("loc", [proj("/h/f", flag=True, items=[item("x", done=True)])])
    assert build([c])["top_todos"] == []


def test_top_todos_unflagged_without_priority_contributes_nothing():
    c = cache("loc", [proj("/h/p", items=[item("a"), item("b")])])
    assert build([c])["top_todos"] == []


def test_top_todos_dedup_priority_item_that_is_also_flag_first():
    c = cache("loc", [proj("/h/f", flag=True, items=[item("最优先 only")])])
    assert titles(build([c])) == ["最优先 only"]


def test_top_todos_capped_at_five():
    projs = [proj("/h/f%d" % i, flag=True, items=[item("t%d" % i)]) for i in range(8)]
    assert len(build([cache("loc", projs)])["top_todos"]) == 5


def test_top_todos_cap_keeps_priority_items_before_flagged():
    projs = [proj("/h/f%d" % i, flag=True, items=[item("t%d" % i)]) for i in range(6)]
    projs.append(proj("/h/p", items=[item("a 最优先")]))
    t = titles(build([cache("loc", projs)]))
    assert len(t) == 5 and t[0] == "a 最优先"


def test_top_todos_empty_when_no_backlogs():
    assert build([cache("loc", [proj("/h/a")])])["top_todos"] == []


def test_top_todos_spans_hosts_with_host_field():
    c1 = cache("loc", [proj("/h/a", flag=True, items=[item("la")])])
    c2 = cache("s-host", [proj("/h/a", flag=True, items=[item("sa")])])
    t = build([c1, c2])["top_todos"]
    assert {(x["host"], x["title"]) for x in t} == {("loc", "la"), ("s-host", "sa")}


# ---------- backlogs ----------

def test_backlogs_entry_shape_and_counts():
    items = [item("a"), item("b", done=True), item("c")]
    c = cache("loc", [proj("/h/a", items=items, mtime=NOW - 500)])
    bl = build([c])["backlogs"]
    assert len(bl) == 1
    e = bl[0]
    for k in ("host", "project", "open", "done", "updated", "items", "raw"):
        assert k in e
    assert e["host"] == "loc" and e["project"] == "/h/a"
    assert e["open"] == 2 and e["done"] == 1
    assert e["updated"] == NOW - 500
    assert [i["title"] for i in e["items"]] == ["a", "b", "c"]
    assert e["raw"] is None


def test_backlogs_raw_passthrough_for_unparsable_file():
    p = proj("/h/a", items=[])
    p["backlog"]["raw"] = "free text"
    e = build([cache("loc", [p])])["backlogs"][0]
    assert e["items"] == [] and e["raw"] == "free text"


def test_backlogs_flagged_first_then_mtime_newest_first():
    c = cache("loc", [
        proj("/h/old", items=[item("x")], mtime=NOW - 9000),
        proj("/h/flag_old", flag=True, items=[item("x")], mtime=NOW - 99999),
        proj("/h/new", items=[item("x")], mtime=NOW - 10),
        proj("/h/flag_new", flag=True, items=[item("x")], mtime=NOW - 5000),
        proj("/h/mid", items=[item("x")], mtime=NOW - 500),
    ])
    order = [e["project"] for e in build([c])["backlogs"]]
    assert order == ["/h/flag_new", "/h/flag_old", "/h/new", "/h/mid", "/h/old"]


def test_backlogs_skips_projects_without_backlog():
    c = cache("loc", [proj("/h/a"), proj("/h/b", items=[item("x")])])
    assert [e["project"] for e in build([c])["backlogs"]] == ["/h/b"]


def test_no_backlog_only_projects_with_session_today_and_no_backlog():
    c = cache("loc", [
        proj("/h/has_session", sessions=[]),
        proj("/h/has_both", items=[item("x")]),
        proj("/h/no_session"),
    ])
    lv = live("loc", [sess("a", "busy", "/h/has_session", started_at=DAY0 + 3600, status="busy"),
                      sess("b", "busy", "/h/has_both", started_at=DAY0 + 3600, status="busy")])
    b = build([c], [lv])
    nb = b["summary"]["no_backlog"]
    assert nb == [{"host": "loc", "project": "/h/has_session"}]


def test_no_backlog_same_path_on_two_hosts_listed_separately():
    c1 = cache("loc", [proj("/h/a")])
    c2 = cache("s-host", [proj("/h/a")])
    lvs = [live("loc", [sess("a", "busy", "/h/a", started_at=DAY0 + 3600, status="busy")]),
           live("s-host", [sess("b", "busy", "/h/a", started_at=DAY0 + 3600, status="busy")])]
    nb = build([c1, c2], lvs)["summary"]["no_backlog"]
    assert sorted((e["host"], e["project"]) for e in nb) == [("loc", "/h/a"), ("s-host", "/h/a")]


def test_tile_git_read_failure_text_and_no_heat():
    p = proj("/h/a", flag=True, git=None)
    p["errors"] = ["git: timeout"]
    t = tile(build([cache("loc", [p])]), "/h/a")
    assert t["text"] == "git 读取失败"
    assert t["commits_14d"] is None


def test_no_backlog_empty_when_no_live():
    c = cache("loc", [proj("/h/a")])
    assert build([c])["summary"]["no_backlog"] == []


# ---------- tiles ----------

def tile(b, path, host="loc"):
    for t in b["tiles"]:
        if t["host"] == host and t["project"] == path:
            return t
    return None


def test_tile_shape_and_state_values():
    c = cache("loc", [proj("/h/a", flag=True)])
    b = build([c])
    t = tile(b, "/h/a")
    for k in ("host", "project", "state", "todo", "commits_14d", "tasks", "text"):
        assert k in t
    for x in b["tiles"]:
        assert x["state"] in ("wait", "busy", "none", "unknown")  # F3: unknown without live data


def test_tiles_inclusion_rules():
    recent_git = {"last_commit_ts": NOW - 2 * DAY, "last_commit_subject": "s", "commit_ts_14d": []}
    c = cache("loc", [
        proj("/h/recent", git=recent_git),
        proj("/h/recent_mtime", last_mtime=NOW - 3 * DAY),
        proj("/h/old_backlog", items=[item("x")]),
        proj("/h/old_flag", flag=True),
        proj("/h/old_plain"),
    ])
    paths = {t["project"] for t in build([c])["tiles"]}
    assert paths == {"/h/recent", "/h/recent_mtime", "/h/old_backlog", "/h/old_flag"}


def test_tiles_recent_by_session_activity():
    p = proj("/h/s", sessions=[{"id": "x", "last_ts": NOW - DAY, "title": "t"}])
    assert tile(build([cache("loc", [p])]), "/h/s") is not None


def test_tile_commits_14d_exactly_14_bins_last_is_today():
    ts = [NOW - 60, NOW - 120, DAY0 - 3600, DAY0 - 13 * DAY + 10]
    g = {"last_commit_ts": NOW - 60, "last_commit_subject": "s", "commit_ts_14d": ts}
    t = tile(build([cache("loc", [proj("/h/a", git=g)])]), "/h/a")
    cm = t["commits_14d"]
    assert len(cm) == 14
    assert cm[-1] == 2 and cm[-2] == 1 and cm[0] == 1
    assert sum(cm) == 4


def test_tile_commits_14d_zeros_when_no_commits():
    c = cache("loc", [proj("/h/none", flag=True, git={"last_commit_ts": 0, "commit_ts_14d": []})])
    assert tile(build([c]), "/h/none")["commits_14d"] == [0] * 14


def test_tile_commits_14d_missing_field_old_cache():
    g = {"last_commit_ts": NOW - DAY}
    t = tile(build([cache("loc", [proj("/h/a", git=g)])]), "/h/a")
    assert t["commits_14d"] == [0] * 14


def test_tile_todo_is_open_count():
    p = proj("/h/a", items=[item("a"), item("b", done=True), item("c")])
    assert tile(build([cache("loc", [p])]), "/h/a")["todo"] == 2


def test_tile_state_unknown_without_live():
    # F3: no live data for the host says nothing about sessions: "unknown", not "none"
    assert tile(build([cache("loc", [proj("/h/a", flag=True)])]), "/h/a")["state"] == "unknown"
    assert tile(build([cache("loc", [proj("/h/a", flag=True)])], [live("loc")]),
                "/h/a")["state"] == "none"


def test_tile_state_wait_busy_none_and_wait_wins():
    c = cache("loc", [proj("/h/w", flag=True), proj("/h/b", flag=True), proj("/h/i", flag=True),
                      proj("/h/both", flag=True), proj("/h/sub", flag=True)])
    lv = live("loc", [
        sess("w", "waiting", "/h/w"),
        sess("b", "busy", "/h/b", status="busy"),
        sess("i", "idle", "/h/i"),
        sess("b2", "busy", "/h/both", status="busy"),
        sess("w2", "waiting", "/h/both"),
        sess("s", "sub", "/h/sub", status="busy", subagents_running=1),
    ])
    b = build([c], [lv])
    assert tile(b, "/h/w")["state"] == "wait"
    assert tile(b, "/h/b")["state"] == "busy"
    assert tile(b, "/h/i")["state"] == "none"
    assert tile(b, "/h/both")["state"] == "wait"
    assert tile(b, "/h/sub")["state"] == "busy"


def test_tile_text_live_session_waiting_uses_question():
    c = cache("loc", [proj("/h/a", items=[item("open todo")])])
    lv = live("loc", [sess("w", "waiting", "/h/a", question="Which db?", title="T")])
    assert tile(build([c], [lv]), "/h/a")["text"] == "Which db?"


def test_tile_text_live_session_beats_open_todo():
    c = cache("loc", [proj("/h/a", items=[item("open todo")])])
    lv = live("loc", [sess("b", "busy", "/h/a", status="busy", title="Refactor parser")])
    assert tile(build([c], [lv]), "/h/a")["text"] == "Refactor parser"


def test_tile_text_first_open_todo_when_no_live():
    c = cache("loc", [proj("/h/a", items=[item("gone", done=True), item("first open"),
                                          item("second")])])
    assert tile(build([c]), "/h/a")["text"] == "first open"


def test_tile_text_recent_commit_when_no_todo():
    g = {"last_commit_ts": NOW - 2 * 3600, "last_commit_subject": "fix the thing",
         "commit_ts_14d": [NOW - 2 * 3600]}
    t = tile(build([cache("loc", [proj("/h/a", git=g)])]), "/h/a")
    assert t["text"].startswith("最近提交")
    assert "fix the thing" in t["text"] and "前" in t["text"]


def test_tile_text_open_todo_beats_commit():
    g = {"last_commit_ts": NOW - 3600, "last_commit_subject": "commit subj", "commit_ts_14d": []}
    p = proj("/h/a", items=[item("todo wins")], git=g)
    assert tile(build([cache("loc", [p])]), "/h/a")["text"] == "todo wins"


def test_tile_text_all_done_backlog_falls_to_commit():
    g = {"last_commit_ts": NOW - 3600, "last_commit_subject": "commit subj", "commit_ts_14d": []}
    p = proj("/h/a", items=[item("x", done=True)], git=g)
    assert "commit subj" in tile(build([cache("loc", [p])]), "/h/a")["text"]


def test_tile_text_not_a_git_repo():
    t = tile(build([cache("loc", [proj("/h/a", flag=True, git=None)])]), "/h/a")
    assert t["text"] == "不是 git 仓库"


def test_tile_per_host_separate_for_same_path():
    c1 = cache("loc", [proj("/h/a", flag=True)])
    c2 = cache("s-host", [proj("/h/a", flag=True)])
    lv = live("s-host", [sess("w", "waiting", "/h/a", question="q")])
    b = build([c1, c2], [live("loc"), lv])  # F3: loc needs live data to say "none"
    assert tile(b, "/h/a", "loc")["state"] == "none"
    assert tile(b, "/h/a", "s-host")["state"] == "wait"


def test_tile_tasks_from_live_session_with_tasks():
    tasks = {"source": "run", "label": "L", "items": [{"id": "T1", "title": "x", "status": "doing",
                                                         "note": ""}], "merges": []}
    c = cache("loc", [proj("/h/a", flag=True)])
    lv = live("loc", [sess("a", "busy", "/h/a", status="busy", tasks=tasks, tasks_owner="a")])
    # shape of the task strip is unspecified (任务条); only pin presence / absence
    assert tile(build([c], [lv]), "/h/a")["tasks"]
    assert tile(build([c], [live("loc", [sess("a", "busy", "/h/a", status="busy")])]),
                "/h/a")["tasks"] in (None, [], {})


# ---------- zellij exited (D15) ----------

def test_zellij_exited_names_cmds_and_count():
    lv = live("loc", [], zellij_exited=["dao", "my s"])
    s = build([cache("loc", [])], [lv])["summary"]
    assert s["zellij_exited_n"] == 2
    assert s["zellij_exited"] == [
        {"host": "loc", "name": "dao", "cmd": "zellij delete-session -- dao"},
        {"host": "loc", "name": "my s", "cmd": "zellij delete-session -- 'my s'"},
    ]


def test_zellij_exited_all_none_is_zero():
    s = build([cache("loc", []), cache("s-host", [])], [live("loc"), live("s-host")])["summary"]
    assert s["zellij_exited_n"] == 0
    assert s["zellij_exited"] == []


def test_zellij_exited_field_absent_old_cache_is_zero():
    lv = live("loc")
    lv.pop("zellij_exited", None)
    s = build([cache("loc", [])], [lv])["summary"]
    assert s["zellij_exited_n"] == 0


def test_zellij_exited_empty_list_is_zero():
    s = build([cache("loc", [])], [live("loc", zellij_exited=[])])["summary"]
    assert s["zellij_exited_n"] == 0 and s["zellij_exited"] == []


def test_zellij_exited_sums_across_hosts_with_host_field():
    lvs = [live("loc", zellij_exited=["a"]), live("s-host", zellij_exited=["b", "c"]),
           live("x", zellij_exited=None)]
    s = build([cache("loc", []), cache("s-host", [])], lvs)["summary"]
    # F3: only zellij_checked hosts count; s-host is remote, its zellij is never collected
    assert s["zellij_exited_n"] == 1
    assert {(e["host"], e["name"]) for e in s["zellij_exited"]} == {("loc", "a")}


def test_zellij_exited_quotes_shell_metacharacters():
    s = build([cache("loc", [])], [live("loc", zellij_exited=["a;b", "it's"])])["summary"]
    cmds = {e["name"]: e["cmd"] for e in s["zellij_exited"]}
    assert cmds["a;b"] == "zellij delete-session -- 'a;b'"
    import shlex
    assert shlex.split(cmds["it's"]) == ["zellij", "delete-session", "--", "it's"]  # F11
