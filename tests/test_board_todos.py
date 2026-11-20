import time

import pytest

from projdeck import board
from tests.test_board_sessions import cache, cfg, live, sess

DAY = 1_800_000_000 // 86400 * 86400
NOW = DAY + 15 * 3600


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def item(title, done=False, text=""):
    return {"title": title, "done": done, "section": None, "text": text or title}


def backlog(items, mtime=NOW - 100):
    return {"path": "docs/backlog.md", "mtime": mtime, "items": items,
            "open": sum(1 for i in items if not i["done"]),
            "done": sum(1 for i in items if i["done"]), "raw": None}


def proj(name, **kw):
    p = {"path": "/home/u/" + name, "exists": True, "git": None, "last_mtime": 0,
         "sessions": [], "flag": False}
    p.update(kw)
    return p


def build(projects, sessions=(), **lvkw):
    lvkw.setdefault("at", NOW - 10)  # fresh: a local host's stale live data is not trusted
    lv = live("a", list(sessions), **lvkw)
    return board.build([cache("a", projects)], [lv], cfg("a"), NOW)


def test_top_todos_marked_first_then_flag_firsts_deduped_capped():
    ps = [proj("f1", flag=True, backlog=backlog([item("done", True), item("f1-first"), item("f1-2")])),
          proj("f2", flag=True, backlog=backlog([item("x", text="最优先 note"), item("f2-first")])),
          proj("n", backlog=backlog([item("n-1"), item("最优先：n-top")]))]
    t = build(ps)["top_todos"]
    assert [(x["project"].split("/")[-1], x["title"]) for x in t] == [
        ("f2", "x"), ("n", "最优先：n-top"), ("f1", "f1-first")]
    assert [x["flag"] for x in t] == [True, False, True]
    assert set(t[0]) == {"host", "project", "title", "flag"}


def test_top_todos_skips_done_and_caps_at_five():
    ps = [proj("p%d" % n, flag=True, backlog=backlog([item("t%d" % n)])) for n in range(7)]
    ps.append(proj("d", flag=True, backlog=backlog([item("最优先 done", True)])))
    t = build(ps)["top_todos"]
    assert len(t) == 5 and all("done" not in x["title"] for x in t)


def test_backlogs_flag_first_then_mtime_desc_and_fields():
    ps = [proj("old", backlog=backlog([item("a")], mtime=10)),
          proj("new", backlog=backlog([item("a"), item("b", True)], mtime=50)),
          proj("flag", flag=True, backlog=backlog([item("a")], mtime=1)),
          proj("none")]
    b = build(ps)["backlogs"]
    assert [x["project"].split("/")[-1] for x in b] == ["flag", "new", "old"]
    new = b[1]
    assert (new["host"], new["open"], new["done"], new["updated"]) == ("a", 1, 1, 50)
    assert len(new["items"]) == 2 and "raw" in new


def test_no_backlog_lists_only_projects_with_session_today():
    ps = [proj("with", backlog=backlog([item("a")])), proj("sess"), proj("quiet"),
          proj("cached", sessions=[{"last_ts": NOW - 60}]), proj("yday", sessions=[{"last_ts": DAY - 60}])]
    b = build(ps, [sess("x", "busy", proj="sess"), sess("y", "busy", proj="with")])
    assert sorted(x["project"] for x in b["summary"]["no_backlog"]) == [
        "/home/u/cached", "/home/u/sess"]
    assert {x["host"] for x in b["summary"]["no_backlog"]} == {"a"}


def test_no_backlog_same_path_on_two_hosts_kept_apart():
    ps = [proj("p", sessions=[{"last_ts": NOW - 60}])]
    b = board.build([cache("a", ps), cache("b", ps)], [], cfg("a", "b"), NOW)
    assert b["summary"]["no_backlog"] == [{"host": "a", "project": "/home/u/p"},
                                          {"host": "b", "project": "/home/u/p"}]


def test_tile_git_failure_is_not_reported_as_non_git_repo():
    ps = [proj("broken", last_mtime=NOW, errors=["git: timed out"]),
          proj("plain", last_mtime=NOW)]
    by = {t["project"].split("/")[-1]: t for t in build(ps)["tiles"]}
    assert by["broken"]["text"] == "git 读取失败" and by["broken"]["commits_14d"] is None
    assert by["plain"]["text"] == "不是 git 仓库" and by["plain"]["commits_14d"] == [0] * 14


def test_tiles_membership_and_commit_bins():
    git = {"commit_ts_14d": [NOW - 10, NOW - 20, DAY - 86400 * 3 + 5], "last_commit_ts": NOW - 10}
    ps = [proj("recent", last_mtime=NOW - 86400 * 2, git=git),
          proj("old", last_mtime=NOW - 86400 * 30),
          proj("bl", last_mtime=1, backlog=backlog([item("t")])),
          proj("fl", last_mtime=1, flag=True)]
    tiles = build(ps)["tiles"]
    assert sorted(t["project"].split("/")[-1] for t in tiles) == ["bl", "fl", "recent"]
    r = next(t for t in tiles if t["project"].endswith("recent"))
    assert len(r["commits_14d"]) == 14 and r["commits_14d"][-1] == 2 and r["commits_14d"][-4] == 1
    assert all(t["state"] in ("wait", "busy", "none") for t in tiles)


def test_tile_state_and_text_chain():
    git = {"commit_ts_14d": [], "last_commit_ts": NOW - 7200, "last_commit_subject": "fix bug"}
    ps = [proj("w", last_mtime=NOW), proj("b", last_mtime=NOW),
          proj("todo", last_mtime=NOW, backlog=backlog([item("d", True), item("next up")])),
          proj("git", last_mtime=NOW, git=git), proj("plain", last_mtime=NOW)]
    ss = [sess("1", "waiting", proj="w", question="proceed?"),
          sess("2", "sub", proj="b", title="building", tasks={"items": [
              {"id": "1", "title": "t", "status": "done"}, {"id": "2", "title": "u", "status": "doing"}]})]
    by = {t["project"].split("/")[-1]: t for t in build(ps, ss)["tiles"]}
    assert (by["w"]["state"], by["w"]["text"]) == ("wait", "proceed?")
    assert (by["b"]["state"], by["b"]["text"], by["b"]["tasks"]) == ("busy", "building", ["done", "doing"])
    assert (by["todo"]["state"], by["todo"]["text"], by["todo"]["todo"]) == ("none", "next up", 1)
    assert by["git"]["text"] == "最近提交 2h 前：fix bug"
    assert by["plain"]["text"] == "不是 git 仓库"


def test_zellij_exited_counts_and_quotes_names():
    # F3: only a host whose zellij is collected (local + mux zellij) is counted
    zcfg = {"hosts": [{"name": "a", "local": True, "mux": "zellij"}]}
    b = board.build([cache("a", [])], [live("a", [], zellij_exited=["dao", "my s"])], zcfg, NOW)
    assert b["summary"]["zellij_exited_n"] == 2
    assert b["summary"]["zellij_exited"] == [
        {"host": "a", "name": "dao", "cmd": "zellij delete-session -- dao"},
        {"host": "a", "name": "my s", "cmd": "zellij delete-session -- 'my s'"}]


def test_zellij_exited_none_is_zero():
    b = build([], zellij_exited=None)
    assert b["summary"]["zellij_exited_n"] == 0 and b["summary"]["zellij_exited"] == []
    assert board.build([], [live("a", [])], cfg("a"), NOW)["summary"]["zellij_exited_n"] == 0
