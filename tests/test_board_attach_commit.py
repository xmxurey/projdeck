"""F1: remote attach commands carry `ssh -t <alias>`; F2: session cards carry the latest commit."""
import shlex

from projdeck import board, web

NOW = 1_800_000_000
P = "/home/u/alpha"
CFG = {"hosts": [{"name": "s-host", "local": True}, {"name": "em", "ssh": "em-alias"}]}


def sess(sid, state, **kw):
    s = {"id": sid, "project": P, "kind": "interactive", "live": True, "name": sid, "title": sid,
         "started_at": NOW - 5000, "status": "idle", "state": state, "idle_since": NOW - 1000,
         "question": "q?", "subagents_running": 0, "segments": [], "attach": "", "errors": []}
    s.update(kw)
    return s


def live(host, sessions, at=NOW - 10, **kw):
    d = {"schema": 1, "host": host, "collected_at": at, "source": "claude agents",
         "sessions": sessions, "projects": {}, "errors": []}
    d.update(kw)
    return d


def cache(host, projects):
    return {"schema": 1, "host": host, "collected_at": NOW - 100, "synced_at": NOW - 100,
            "projects": projects}


def project(git=None, errors=()):
    return {"path": P, "exists": True, "last_mtime": NOW - 100, "sessions": [], "git": git,
            "errors": list(errors)}


# ---- F1

def test_remote_attach_gets_ssh_prefix():
    lv = live("em", [sess("w", "waiting", attach="tmux attach -t w")])
    w = board.build([], [lv], CFG, NOW)["waiting"][0]
    assert w["attach"] == "ssh -t em-alias 'tmux attach -t w'"
    assert shlex.split(w["attach"]) == ["ssh", "-t", "em-alias", "tmux attach -t w"]


def test_local_attach_has_no_prefix():
    lv = live("s-host", [sess("w", "waiting", attach="zellij attach z")])
    assert board.build([], [lv], CFG, NOW)["waiting"][0]["attach"] == "zellij attach z"


def test_stale_host_sessions_get_the_prefix_too():
    lv = live("em", [sess("b", "busy", status="busy", attach="zellij attach z")], at=NOW - 1000)
    s = board.build([], [lv], CFG, NOW)["stale_host_sessions"][0]
    assert s["attach"] == "ssh -t em-alias 'zellij attach z'"


def test_empty_attach_stays_empty_and_host_without_alias_unchanged():
    lv = live("em", [sess("w", "waiting", attach="")])
    assert board.build([], [lv], CFG, NOW)["waiting"][0]["attach"] == ""
    lv = live("xx", [sess("w", "waiting", attach="tmux attach -t w")])
    assert board.build([], [lv], CFG, NOW)["waiting"][0]["attach"] == "tmux attach -t w"


def test_web_card_shows_prefixed_command():
    lv = live("em", [sess("w", "waiting", attach="tmux attach -t w")])
    html = web.render_columns(board.build([], [lv], CFG, NOW))
    assert "ssh -t em-alias &#x27;tmux attach -t w&#x27;" in html


# ---- F2

def test_last_commit_from_live_projects_first():
    lv = live("s-host", [sess("w", "waiting")],
              projects={P: {"last_commit": {"ts": NOW - 600, "subject": "live one"}, "commits": []}})
    c = cache("s-host", [project(git={"last_commit_ts": NOW - 9000, "last_commit_subject": "old"})])
    w = board.build([c], [lv], CFG, NOW)["waiting"][0]
    assert w["last_commit"] == {"ts": NOW - 600, "subject": "live one"}
    assert w["git_failed"] is False


def test_last_commit_falls_back_to_collect_cache():
    c = cache("s-host", [project(git={"last_commit_ts": NOW - 9000, "last_commit_subject": "old"})])
    b = board.build([c], [live("s-host", [sess("b", "busy", status="busy")])], CFG, NOW)
    assert b["busy"][0]["last_commit"] == {"ts": NOW - 9000, "subject": "old"}


def test_last_commit_none_on_git_failure():
    lv = live("s-host", [sess("w", "waiting")],
              projects={P: {"last_commit": None, "commits": None, "error": "git: boom"}})
    w = board.build([], [lv], CFG, NOW)["waiting"][0]
    assert w["last_commit"] is None and w["git_failed"] is True
    c = cache("s-host", [project(git=None, errors=["git: timed out"])])
    w = board.build([c], [live("s-host", [sess("w", "waiting")])], CFG, NOW)["waiting"][0]
    assert w["last_commit"] is None and w["git_failed"] is True


def test_last_commit_none_without_commits_is_not_a_failure():
    c = cache("s-host", [project(git={"last_commit_ts": 0, "last_commit_subject": ""})])
    w = board.build([c], [live("s-host", [sess("w", "waiting")])], CFG, NOW)["waiting"][0]
    assert w["last_commit"] is None and w["git_failed"] is False


def cards(lv, c=()):
    return web.render_columns(board.build(list(c), [lv], CFG, NOW))


def test_wait_and_busy_cards_show_latest_commit():
    pj = {P: {"last_commit": {"ts": NOW - 7200, "subject": "fix <x>"}, "commits": []}}
    html = cards(live("s-host", [sess("w", "waiting")], projects=pj))
    assert "最近提交 2h 前：fix &lt;x&gt;" in html
    html = cards(live("s-host", [sess("b", "busy", status="busy")], projects=pj))
    assert "最近提交 2h 前：fix &lt;x&gt;" in html


def test_cards_say_git_failed_or_dash():
    pj = {P: {"last_commit": None, "commits": None, "error": "git: boom"}}
    assert "最近提交：git 读取失败" in cards(live("s-host", [sess("w", "waiting")], projects=pj))
    pj = {P: {"last_commit": None, "commits": []}}
    assert "最近提交：—" in cards(live("s-host", [sess("b", "busy", status="busy")], projects=pj))


def test_busy_card_has_no_empty_placeholder_left():
    html = web._busy_card({}, sess("b", "busy", status="busy"), NOW)
    assert "最近提交：—" in html
