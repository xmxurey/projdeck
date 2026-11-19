"""T28: terminal integration of the dashboard: ^L backlog, ^V dashboard, live data fetching."""
import itertools
import json
import os
import time

import pytest

from projdeck import cli, opener, sync, ui

REAL_FRESH_LIVES = cli.fresh_lives
REAL_FRESH_CACHES = cli.fresh_caches
REAL_BACKGROUND_LIVE_SYNC = cli.background_live_sync  # conftest replaces it per test
NOW = 1_800_000_000
CFG = {"hosts": [{"name": "mac", "local": True}, {"name": "vm", "ssh": "vm"}],
       "tiers_days": {"active": 14, "stale": 90}, "auto_sync_minutes": 30,
       "dashboard_url": None, "serve": {"port": 8787}, "ssh_timeout": 5}


def project(path, **kw):
    p = {"path": path, "exists": True, "git": None, "suspicious": [], "progress": "", "errors": [],
         "sessions": [], "last_mtime": NOW - 100}
    p.update(kw)
    return p


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    return tmp_path


def put_live(name, collected_at, sessions=()):
    sync._write_json(sync._live_path(name), {
        "schema": 1, "host": name, "collected_at": collected_at, "synced_at": collected_at,
        "stale_since": None, "last_error": None, "sessions": list(sessions), "projects": {},
        "zellij_exited": None, "errors": []})


def marker():
    return os.path.join(sync.cache_dir(), ".syncing-live")


# ---- fresh_lives

@pytest.fixture
def spy(monkeypatch):
    """Real background_live_sync with Popen stubbed (the marker logic runs for real)."""
    monkeypatch.setattr(cli, "background_live_sync", REAL_BACKGROUND_LIVE_SYNC)
    calls = {"sync_live": [], "popen": [], "ssh": []}
    monkeypatch.setattr(cli.sync, "sync_live",
                        lambda host, cfg, now=None, memo=None: calls["sync_live"].append(host["name"]))
    monkeypatch.setattr(cli.sync, "run_agent", lambda *a, **k: calls["ssh"].append(a))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, **k: calls["popen"].append(argv))
    return calls


def test_local_live_older_than_30s_is_recomputed_in_process(cache_env, spy):
    put_live("mac", NOW - 40)
    put_live("vm", NOW - 10)
    cli.fresh_lives(CFG, NOW)
    assert spy["sync_live"] == ["mac"]
    assert spy["popen"] == [] and spy["ssh"] == []


def test_local_live_10s_old_is_not_recomputed(cache_env, spy):
    put_live("mac", NOW - 10)
    put_live("vm", NOW - 10)
    cli.fresh_lives(CFG, NOW)
    assert spy["sync_live"] == [] and spy["popen"] == []


def test_remote_live_3min_old_starts_one_background_sync(cache_env, spy):
    put_live("mac", NOW - 5)
    put_live("vm", NOW - 180)
    lives = cli.fresh_lives(CFG, NOW)
    assert len(spy["popen"]) == 1
    argv = spy["popen"][0]
    assert argv[-5:] == ["-m", "projdeck", "sync", "--live", "--quiet"]
    assert spy["sync_live"] == [] and spy["ssh"] == []
    assert {lv["host"] for lv in lives} == {"mac", "vm"}  # returns what is cached, no waiting
    assert os.path.exists(marker())


def test_fresh_marker_blocks_second_background_sync(cache_env, spy):
    now = time.time()
    put_live("mac", now - 5)
    put_live("vm", now - 180)
    os.makedirs(sync.cache_dir(), exist_ok=True)
    with open(marker(), "w"):
        pass
    cli.fresh_lives(CFG, now)
    assert spy["popen"] == []


def test_old_marker_does_not_block(cache_env, spy):
    now = time.time()
    put_live("mac", now - 5)
    put_live("vm", now - 180)
    os.makedirs(sync.cache_dir(), exist_ok=True)
    with open(marker(), "w"):
        pass
    os.utime(marker(), (now - 400, now - 400))
    cli.fresh_lives(CFG, now)
    assert len(spy["popen"]) == 1


def test_missing_remote_live_triggers_background_sync(cache_env, spy):
    put_live("mac", NOW - 5)
    cli.fresh_lives(CFG, NOW)
    assert len(spy["popen"]) == 1


def test_fresh_lives_asks_for_background_sync_only_when_remote_is_stale(cache_env, live_sync_spawns, monkeypatch):
    monkeypatch.setattr(cli.sync, "sync_live", lambda *a, **k: None)
    put_live("mac", NOW - 5)
    put_live("vm", NOW - 10)
    cli.fresh_lives(CFG, NOW)
    assert live_sync_spawns == []
    put_live("vm", NOW - 180)
    cli.fresh_lives(CFG, NOW)
    assert live_sync_spawns == [1]


def test_both_background_syncs_use_the_one_marker_window(cache_env, spy):
    """.syncing and .syncing-live are fresh for the same named window, decided in sync.py."""
    now = time.time()
    for name in (".syncing", ".syncing-live"):
        os.makedirs(sync.cache_dir(), exist_ok=True)
        path = os.path.join(sync.cache_dir(), name)
        with open(path, "w"):
            pass
        os.utime(path, (now - sync.MARKER_SECONDS + 5,) * 2)
        assert sync.marker_fresh(name, now)
        os.utime(path, (now - sync.MARKER_SECONDS - 5,) * 2)
        assert not sync.marker_fresh(name, now)


def test_background_sync_respects_fresh_marker_and_claims_it(cache_env, spy):
    cli.background_sync()
    assert len(spy["popen"]) == 1 and spy["popen"][0][-2:] == ["sync", "--quiet"]
    cli.background_sync()
    assert len(spy["popen"]) == 1  # the marker it just claimed blocks the second


def test_stale_marker_is_replaced_not_stuck(cache_env, spy):
    os.makedirs(sync.cache_dir(), exist_ok=True)
    with open(marker(), "w"):
        pass
    old = time.time() - sync.MARKER_SECONDS - 10
    os.utime(marker(), (old, old))
    cli.background_live_sync()
    assert len(spy["popen"]) == 1
    assert time.time() - os.path.getmtime(marker()) < 5


def test_claim_marker_is_exclusive(cache_env):
    assert sync.claim_marker(".syncing-live") is True
    assert sync.claim_marker(".syncing-live") is False


def test_popen_failure_removes_marker_and_gives_notice(cache_env, monkeypatch):
    monkeypatch.setattr(cli, "background_live_sync", REAL_BACKGROUND_LIVE_SYNC)

    def boom(*a, **k):
        raise OSError("no exec")
    monkeypatch.setattr(cli.subprocess, "Popen", boom)
    msg = cli.background_live_sync()
    assert msg and "no exec" in msg
    assert not os.path.exists(marker())


def test_live_sync_running_gets_the_callers_clock(cache_env, spy, monkeypatch):
    seen = []
    monkeypatch.setattr(cli.sync, "live_sync_running", lambda now=None: seen.append(now) or True)
    put_live("mac", NOW - 5)
    cli.fresh_lives(CFG, NOW)
    assert seen == [NOW]


def test_browse_survives_background_spawn_failure(cache_env, monkeypatch, capsys):
    browse_env(monkeypatch, [project("/a")], [])
    monkeypatch.setattr(cli, "fresh_lives", REAL_FRESH_LIVES)
    monkeypatch.setattr(cli, "background_live_sync", REAL_BACKGROUND_LIVE_SYNC)
    monkeypatch.setattr(cli.sync, "sync_live", lambda *a, **k: None)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert cli.cmd_browse(dict(CFG), None) == 0


# ---- cmd_browse: ^L and ^V

def browse_env(monkeypatch, rows_cache, picks):
    cache = {"host": "vm", "projects": rows_cache}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: [cache])
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: [])
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: [cache])
    it = iter(picks)
    seen = []

    def fake_pick(rows, now, notice=None, select=None, board=None):
        seen.append({"notice": notice, "select": select, "board": board})
        try:
            return next(it)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", fake_pick)
    return seen


def test_browse_passes_board_built_from_lives(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a")], [])
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: [
        {"host": "vm", "collected_at": time.time(), "sessions": [], "projects": {}}])
    cli.cmd_browse(dict(CFG), None)
    b = seen[0]["board"]
    assert b is not None and {"summary", "waiting", "hosts"} <= set(b)
    assert [h["name"] for h in b["hosts"]] == ["mac", "vm"]
    assert [h["live_at"] for h in b["hosts"]][0] is None and b["hosts"][1]["live_at"]


def test_ctrl_l_opens_backlog_and_returns_with_cursor_on_chosen(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a"), project("/b")], [("ctrl-l", "vm", "/a")])
    monkeypatch.setattr(ui, "pick_backlog", lambda rows, now: ("vm", "/b"))
    assert cli.cmd_browse(dict(CFG), None) == 0
    assert len(seen) == 2 and seen[1]["select"] == ("vm", "/b")


def test_ctrl_l_escape_returns_to_overview(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a"), project("/b")], [("ctrl-l", "vm", "/a")])

    def esc(rows, now):
        raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_backlog", esc)
    assert cli.cmd_browse(dict(CFG), None) == 0
    assert len(seen) == 2 and seen[1]["select"] == ("vm", "/a")


def test_ctrl_v_opens_configured_url(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a")], [("ctrl-v", "vm", "/a")])
    opened = []
    monkeypatch.setattr(opener, "open_url", lambda url: opened.append(url) or True)
    cli.cmd_browse(dict(CFG, dashboard_url="http://100.1.2.3:8787"), None)
    assert opened == ["http://100.1.2.3:8787"]
    assert len(seen) == 2 and seen[1]["select"] == ("vm", "/a")


def test_ctrl_v_without_gui_shows_url_in_notice(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a")], [("ctrl-v", "vm", "/a")])
    for var in ("DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(opener.sys, "platform", "linux")
    ran = []
    monkeypatch.setattr(opener.subprocess, "Popen", lambda *a, **k: ran.append(a))
    cli.cmd_browse(dict(CFG, dashboard_url="http://100.1.2.3:8787"), None)
    assert "http://100.1.2.3:8787" in seen[1]["notice"]
    assert ran == []


def test_ctrl_v_falls_back_to_tailscale_ip_and_serve_port(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a")], [("ctrl-v", "vm", "/a")])
    monkeypatch.setattr(cli.serve, "tailscale_self",
                        lambda: {"ip": "100.64.0.9", "dns_name": "x.ts.net", "host_name": "x"})
    opened = []
    monkeypatch.setattr(opener, "open_url", lambda url: opened.append(url) or True)
    cli.cmd_browse(dict(CFG, serve={"port": 9000}), None)
    assert opened == ["http://100.64.0.9:9000"]


def test_ctrl_v_without_any_url_says_how_to_configure(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a")], [("ctrl-v", "vm", "/a")])
    monkeypatch.setattr(cli.serve, "tailscale_self", lambda: None)
    opened = []
    monkeypatch.setattr(opener, "open_url", lambda url: opened.append(url) or True)
    cli.cmd_browse(dict(CFG), None)
    assert opened == []
    assert "set dashboard_url in ~/.config/proj/config.json" in seen[1]["notice"]


def test_open_url_uses_open_on_macos_and_xdg_open_with_display(monkeypatch):
    ran = []

    def popen(argv, **k):
        ran.append((argv, k))
    monkeypatch.setattr(opener.subprocess, "Popen", popen)
    monkeypatch.setattr(opener.sys, "platform", "darwin")
    assert opener.open_url("http://h:1") is True and ran[-1][0] == ["open", "http://h:1"]
    monkeypatch.setattr(opener.sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert opener.open_url("https://h:1") is True and ran[-1][0] == ["xdg-open", "https://h:1"]
    assert ran[-1][1].get("start_new_session") is True  # never waits for the browser


def test_open_url_false_when_launch_fails(monkeypatch):
    monkeypatch.setattr(opener.sys, "platform", "darwin")

    def boom(*a, **k):
        raise OSError("no open")
    monkeypatch.setattr(opener.subprocess, "Popen", boom)
    assert opener.open_url("http://h:1") is False


@pytest.mark.parametrize("url", ["-a Calculator", "file:///etc/passwd", "javascript:alert(1)", ""])
def test_open_url_only_takes_http_urls(monkeypatch, url):
    ran = []
    monkeypatch.setattr(opener.subprocess, "Popen", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(opener.sys, "platform", "darwin")
    assert opener.open_url(url) is False and ran == []


def test_open_url_false_without_gui(monkeypatch):
    monkeypatch.setattr(opener.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert opener.open_url("http://h:1") is False


# ---- rulings: uncollected rows

def test_enter_on_uncollected_project_says_to_sync(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a")], [("", "vm", "/only-in-live")])
    cli.cmd_browse(dict(CFG), None)
    assert "还没采集" in seen[1]["notice"] and "^U" in seen[1]["notice"]
    assert seen[1]["select"] == ("vm", "/only-in-live")


@pytest.mark.parametrize("key", ["ctrl-y", "ctrl-r", "ctrl-x"])
def test_other_row_actions_on_uncollected_project_say_to_sync(monkeypatch, key):
    seen = browse_env(monkeypatch, [project("/a")], [(key, "vm", "/only-in-live")])
    monkeypatch.setattr(cli, "clean", lambda *a: pytest.fail("clean must not run"))
    cli.cmd_browse(dict(CFG), None)
    assert "还没采集" in seen[1]["notice"]


def test_every_round_refreshes_now_for_lives_and_board(monkeypatch):
    clock = itertools.count(1000.0, 500.0)  # never runs out when more reads of the time appear
    monkeypatch.setattr(cli.time, "time", lambda: next(clock))
    seen = browse_env(monkeypatch, [project("/a")], [("ctrl-v", "vm", "/a")])
    asked = []
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: asked.append(now) or [])
    built = []
    real_build = cli.board.build
    monkeypatch.setattr(cli.board, "build", lambda c, l, cfg, now: built.append(now) or real_build(c, l, cfg, now))
    monkeypatch.setattr(cli.serve, "tailscale_self", lambda: None)
    assert cli.cmd_browse(dict(CFG), None) == 0
    assert len(asked) == 2 and asked[1] > asked[0] and built == asked


def test_host_header_line_stays_silent(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a")], [("", "vm", "")])
    cli.cmd_browse(dict(CFG), None)
    assert seen[1]["notice"] is None


def test_unbound_key_returns_to_list(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a")], [("ctrl-q", "vm", "/a")])
    cli.cmd_browse(dict(CFG), None)
    assert len(seen) == 2


# ---- preview subprocesses

def preview_env(tmp_path, monkeypatch, backlog=None):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"hosts": [{"name": "vm", "ssh": "vm"}]}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfgp))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    p = project("/home/u/quant")
    if backlog:
        p["backlog"] = backlog
    sync._write_cache("vm", {"schema": 1, "host": "vm", "collected_at": time.time(), "synced_at": time.time(),
                             "projects": [p]})


def live_session(project_path, now):
    return {"id": "s1", "project": project_path, "state": "busy", "name": "q", "title": "t",
            "started_at": now - 3600, "idle_since": now - 60, "last_ts": now - 60,
            "segments": [["busy", now - 3600, now - 60]], "tasks": None, "question": "",
            "last_reply": "hello there", "errors": []}


def test_preview_shows_live_six_hour_section(tmp_path, monkeypatch, capsys):
    preview_env(tmp_path, monkeypatch)
    now = time.time()
    put_live("vm", now, [live_session("/home/u/quant", now)])
    assert cli.main(["_preview", "vm", "/home/u/quant\t"]) == 0
    out = capsys.readouterr().out
    assert "最近 6 小时" in out and "hello there" in out


def test_preview_of_project_only_in_live_uses_minimal_row(tmp_path, monkeypatch, capsys):
    preview_env(tmp_path, monkeypatch)
    now = time.time()
    put_live("vm", now, [live_session("/home/u/new", now)])
    assert cli.main(["_preview", "vm", "/home/u/new"]) == 0
    out = capsys.readouterr().out
    assert "最近 6 小时" in out and "not in cache" not in out


def test_preview_of_unknown_project_says_not_in_cache(tmp_path, monkeypatch, capsys):
    preview_env(tmp_path, monkeypatch)
    assert cli.main(["_preview", "vm", "/nowhere"]) == 0
    assert "(not in cache)" in capsys.readouterr().out


def test_preview_never_spawns_or_syncs(tmp_path, monkeypatch, capsys, spy):
    preview_env(tmp_path, monkeypatch)
    cli.main(["_preview", "vm", "/home/u/quant"])
    assert spy["sync_live"] == [] and spy["popen"] == [] and spy["ssh"] == []


BL = {"path": "docs/backlog.md", "mtime": 5, "open": 1, "done": 0, "raw": "- [ ] ship it\n",
      "items": [{"title": "ship it", "section": "now", "done": False, "text": "ship the thing, fully"}]}


def test_bpreview_gives_item_body(tmp_path, monkeypatch, capsys):
    preview_env(tmp_path, monkeypatch, BL)
    assert cli.main(["_bpreview", "vm", "/home/u/quant", "0"]) == 0
    assert "ship the thing, fully" in capsys.readouterr().out


def test_bpreview_accepts_empty_index(tmp_path, monkeypatch, capsys):
    preview_env(tmp_path, monkeypatch, BL)
    assert cli.main(["_bpreview", "vm", "/home/u/quant", ""]) == 0
    out = capsys.readouterr().out
    assert "ship it" in out and "Traceback" not in out


# ---- T31 carry-over from the T28 review (M-2 .. M-4)

def test_failed_background_sync_notice_reaches_the_header(monkeypatch, cache_env):
    seen = browse_env(monkeypatch, [project("/a")], [])
    monkeypatch.setattr(cli, "fresh_caches", REAL_FRESH_CACHES)
    monkeypatch.setattr(cli.sync, "oldest_sync", lambda cfg: 1.0)
    monkeypatch.setattr(cli, "background_sync", lambda now=None: "could not start background sync: boom")
    cli.cmd_browse(dict(CFG), None)
    assert "could not start background sync: boom" in seen[0]["notice"]


def test_failed_live_sync_notice_reaches_the_header_not_stderr(monkeypatch, cache_env, capsys):
    seen = browse_env(monkeypatch, [project("/a")], [])
    monkeypatch.setattr(cli, "fresh_lives", REAL_FRESH_LIVES)
    monkeypatch.setattr(cli.sync, "sync_live", lambda *a, **k: None)
    monkeypatch.setattr(cli, "background_live_sync", lambda now=None: "could not start \x1b[31mlive")
    put_live("vm", time.time() - 1000)
    put_live("mac", time.time())
    cli.cmd_browse(dict(CFG), None)
    assert "could not start" in seen[0]["notice"] and "\x1b" not in seen[0]["notice"]
    assert capsys.readouterr().err == ""


def test_claim_marker_os_error_is_not_claimed_and_reported(monkeypatch, cache_env):
    (cache_env / "blocked").write_text("a file, so the cache directory cannot be made under it")
    monkeypatch.setenv("PROJ_CACHE", str(cache_env / "blocked" / "cache"))
    problems = []
    assert sync.claim_marker(".syncing-live", None, problems) is False
    assert len(problems) == 1 and problems[0]
    assert sync.claim_marker(".syncing-live") is False  # also fine without a sink


def test_claim_marker_permission_error_is_not_claimed(monkeypatch, cache_env):
    real = os.open

    def deny(path, *a, **k):
        if str(path).endswith(".syncing-live"):
            raise PermissionError(13, "Permission denied")
        return real(path, *a, **k)
    monkeypatch.setattr(sync.os, "open", deny)
    problems = []
    assert sync.claim_marker(".syncing-live", None, problems) is False
    assert "Permission denied" in problems[0]


def test_background_sync_survives_unwritable_cache_and_returns_a_notice(monkeypatch, cache_env):
    (cache_env / "cache").write_text("a file where the cache directory should be")
    out = cli.background_sync(NOW)
    assert out and "background sync not started" in out


def test_dashboard_url_that_is_not_http_says_so(monkeypatch):
    seen = browse_env(monkeypatch, [project("/a")], [("ctrl-v", "vm", "/a")])
    cli.cmd_browse(dict(CFG, dashboard_url="ftp://x:1"), None)
    n = seen[1]["notice"]
    assert "ftp://x:1" in n and "Mac" not in n and "http" in n


def test_live_notice_shows_in_its_own_round_only(monkeypatch, cache_env):
    seen = browse_env(monkeypatch, [project("/a")], [("ctrl-q", "vm", "/a"), ("ctrl-q", "vm", "/a")])
    rounds = []

    def lives(cfg, now):
        rounds.append(1)
        if len(rounds) == 2:
            cli._note("live sync broke")
        return []
    monkeypatch.setattr(cli, "fresh_lives", lives)
    cli.cmd_browse(dict(CFG), None)
    assert len(seen) == 3
    assert "live sync broke" not in (seen[0]["notice"] or "")
    assert "live sync broke" in seen[1]["notice"]
    assert "live sync broke" not in (seen[2]["notice"] or "")


@pytest.mark.parametrize("command", ["cmd_sessions", "cmd_grep"])
def test_sessions_and_grep_print_refresh_problems_to_stderr(monkeypatch, capsys, command):
    cache = {"host": "vm", "projects": [project("/a")]}

    def caches(cfg, now):
        cli._note("could not start background sync: boom")
        return [cache]
    monkeypatch.setattr(cli, "fresh_caches", caches)
    monkeypatch.setattr(cli.model, "all_sessions", lambda rows: [])
    monkeypatch.setattr(cli.model, "all_prompts", lambda rows: [])
    args = type("A", (), {"query": []})()
    getattr(cli, command)(dict(CFG), args)
    assert "could not start background sync: boom" in capsys.readouterr().err
