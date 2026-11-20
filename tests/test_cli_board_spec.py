"""Independent spec tests for T28: terminal ^L backlog, ^V dashboard, fresh_lives, and the
_preview / _bpreview subcommands.  Written from the brief and rulings only."""
import copy
import json
import os
import subprocess
import time

import pytest

from projdeck import cli, config, opener, serve, sync, ui

NOW = int(time.time())  # the marker file and the preview subprocess use the wall clock


# ---------------------------------------------------------------- helpers
def cfg_with(hosts=None, **kw):
    c = copy.deepcopy(config.DEFAULTS)
    c["hosts"] = hosts or [{"name": "vm", "ssh": "vm"}]
    c.update(kw)
    return c


def live_doc(host, collected_at, sessions=()):
    return {"schema": 1, "host": host, "collected_at": collected_at, "synced_at": collected_at,
            "stale_since": None, "last_error": None, "sessions": list(sessions),
            "projects": {}, "zellij_exited": None, "errors": []}


def sess(project, host="vm", state="busy", **kw):
    d = {"id": "s1", "project": project, "cwd": project, "latest_cwd": project,
         "kind": "interactive", "live": True, "name": "n-s1", "title": "", "host": host,
         "started_at": NOW - 5000, "status": "busy", "state": state, "idle_since": None,
         "question": "", "last_reply": "", "last_human": {"ts": 0, "text": ""},
         "last_input": "human", "subagents_running": 0, "segments": [], "attach": "",
         "tasks": None, "tasks_owner": None, "errors": []}
    d.update(kw)
    return d


def write_live(host, doc):
    p = os.path.join(sync.cache_dir(), "live", host + ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(doc, f)


def write_cache(host, projects):
    os.makedirs(sync.cache_dir(), exist_ok=True)
    with open(os.path.join(sync.cache_dir(), host + ".json"), "w") as f:
        json.dump({"schema": 1, "host": host, "collected_at": NOW, "synced_at": NOW,
                   "stale_since": None, "last_error": None, "projects": projects}, f)


@pytest.fixture(autouse=True)
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


@pytest.fixture
def no_ssh(monkeypatch):
    """Any ssh, or any real process, during the test is an error."""
    def boom(*a, **k):
        raise AssertionError("unexpected process / ssh: %r" % (a[:1],))
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)
    monkeypatch.setattr(sync, "run_agent", boom)


@pytest.fixture
def sync_live_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "sync_live",
                        lambda host, cfg, now=None, memo=None: calls.append(host["name"]) or
                        {"host": host["name"], "ok": True, "error": None})
    return calls


# ---------------------------------------------------------------- browse harness
def browse(monkeypatch, picks, cfg=None, lives=(), projects=None):
    cfg = cfg or cfg_with()
    cache = {"host": "vm", "projects": projects if projects is not None else
             [{"path": "/p", "last_mtime": NOW - 100}]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: [cache])
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: [cache])
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: list(lives))
    it = iter(picks)
    seen = []

    def fake_pick(rows, now, notice=None, select=None, board=None):
        seen.append({"notice": notice, "select": select, "board": board})
        try:
            return next(it)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", fake_pick)
    launched = []
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: launched.append(path) or "iterm")
    assert cli.cmd_browse(cfg, None) == 0
    return seen, launched


# ---------------------------------------------------------------- AC1: ^L
def test_ctrl_l_calls_pick_backlog_and_returns_with_cursor(monkeypatch):
    got = []

    def fake_backlog(rows, now):
        got.append([(r["host"], r["path"]) for r in rows])
        return "vm", "/p"
    monkeypatch.setattr(ui, "pick_backlog", fake_backlog)
    seen, launched = browse(monkeypatch, [("ctrl-l", "vm", "/other")])
    assert got == [[("vm", "/p")]]
    assert len(seen) == 2 and seen[1]["select"] == ("vm", "/p")
    assert launched == []


def test_ctrl_l_escape_returns_to_overview(monkeypatch):
    def cancel(rows, now):
        raise ui.Cancelled()
    called = []
    monkeypatch.setattr(ui, "pick_backlog", lambda rows, now: called.append(1) or cancel(rows, now))
    seen, launched = browse(monkeypatch, [("ctrl-l", "vm", "/p")])
    assert called == [1] and len(seen) == 2 and launched == []


# ---------------------------------------------------------------- AC2: ^V
def test_ctrl_v_opens_configured_url(monkeypatch):
    urls = []
    monkeypatch.setattr(opener, "open_url", lambda url: urls.append(url) or True)
    cfg = cfg_with(dashboard_url="http://dash.example:1234")
    seen, _ = browse(monkeypatch, [("ctrl-v", "vm", "/p")], cfg=cfg)
    assert urls == ["http://dash.example:1234"]
    assert len(seen) == 2


def test_ctrl_v_without_display_mentions_url_in_notice(monkeypatch):
    monkeypatch.setattr(opener, "open_url", lambda url: False)
    cfg = cfg_with(dashboard_url="http://dash.example:1234")
    seen, _ = browse(monkeypatch, [("ctrl-v", "vm", "/p")], cfg=cfg)
    assert "http://dash.example:1234" in (seen[1]["notice"] or "")


def test_ctrl_v_no_url_available_tells_how_to_configure(monkeypatch):
    urls = []
    monkeypatch.setattr(opener, "open_url", lambda url: urls.append(url) or True)
    monkeypatch.setattr(serve, "tailscale_self", lambda: None)
    seen, _ = browse(monkeypatch, [("ctrl-v", "vm", "/p")])
    assert urls == []
    assert "set dashboard_url in ~/.config/proj/config.json" in (seen[1]["notice"] or "")


def test_ctrl_v_falls_back_to_tailscale_ip_and_serve_port(monkeypatch):
    urls = []
    monkeypatch.setattr(opener, "open_url", lambda url: urls.append(url) or True)
    monkeypatch.setattr(serve, "tailscale_self",
                        lambda: {"ip": "100.64.1.2", "dns_name": "x.ts.net", "host_name": "x"})
    cfg = cfg_with(serve={"bind": None, "port": 8899, "push_statusline": True})
    browse(monkeypatch, [("ctrl-v", "vm", "/p")], cfg=cfg)
    assert [u.rstrip("/") for u in urls] == ["http://100.64.1.2:8899"]


def test_ctrl_v_configured_url_wins_over_tailscale(monkeypatch):
    urls = []
    monkeypatch.setattr(opener, "open_url", lambda url: urls.append(url) or True)

    def boom():
        raise AssertionError("tailscale must not be asked when dashboard_url is set")
    monkeypatch.setattr(serve, "tailscale_self", boom)
    browse(monkeypatch, [("ctrl-v", "vm", "/p")], cfg=cfg_with(dashboard_url="http://c:1"))
    assert urls == ["http://c:1"]


def test_ctrl_v_notice_is_sanitised(monkeypatch):
    monkeypatch.setattr(opener, "open_url", lambda url: False)
    cfg = cfg_with(dashboard_url="http://h/\x1b]0;pwn\x07x\x1b[31m")
    seen, _ = browse(monkeypatch, [("ctrl-v", "vm", "/p")], cfg=cfg)
    n = seen[1]["notice"] or ""
    assert "\x1b" not in n and "\x07" not in n


def test_open_url_returns_false_without_display(monkeypatch):
    spawned = []
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, lambda *a, **k: spawned.append(a))
    if os.uname().sysname == "Darwin":
        pytest.skip("macOS always has `open`")
    assert opener.open_url("http://x:1") is False
    assert spawned == []


def test_open_url_with_display_launches_a_browser_opener(monkeypatch):
    if os.uname().sysname == "Darwin":
        pytest.skip("platform specific")
    monkeypatch.setenv("DISPLAY", ":0")
    spawned = []

    class P:
        returncode = 0
        stdout = stderr = ""

        def __init__(self, *a, **k):
            spawned.append(a[0] if a else k.get("args"))

        def wait(self, *a, **k):
            return 0

        def poll(self):
            return 0
    monkeypatch.setattr(subprocess, "Popen", P)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: spawned.append(a[0]) or P())
    monkeypatch.setattr(subprocess, "call", lambda *a, **k: spawned.append(a[0]) or 0)
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: "/usr/bin/" + name)
    assert opener.open_url("http://x:1") is True
    assert spawned and "http://x:1" in " ".join(map(str, spawned[0]))


# ---------------------------------------------------------------- ruling 2/3: board + minimal row
def test_browse_passes_a_board_to_pick_project(monkeypatch):
    lives = [live_doc("vm", NOW, [sess("/p")])]
    seen, _ = browse(monkeypatch, [], lives=lives)
    b = seen[0]["board"]
    assert isinstance(b, dict) and "summary" in b and "hosts" in b
    assert b["summary"]["busy"] == 1


@pytest.mark.parametrize("key", ["", "ctrl-r", "ctrl-y"])
def test_uncollected_row_gives_notice_not_silence(monkeypatch, key):
    lives = [live_doc("vm", NOW, [sess("/new/proj")])]
    seen, launched = browse(monkeypatch, [(key, "vm", "/new/proj")], lives=lives)
    assert launched == []
    assert len(seen) == 2, "must return to the list"
    assert "该项目还没采集，按 ^U 同步" in (seen[1]["notice"] or "")


@pytest.mark.parametrize("key", ["", "ctrl-r"])
def test_collected_row_does_not_get_uncollected_notice(monkeypatch, key):
    lives = [live_doc("vm", NOW, [sess("/p")])]
    seen, launched = browse(monkeypatch, [(key, "vm", "/p")], lives=lives)
    if key == "":
        assert launched == ["/p"]
    assert "还没采集" not in " ".join(str(s["notice"]) for s in seen)


# ---------------------------------------------------------------- AC3: fresh_lives
HOSTS = [{"name": "loc", "local": True}, {"name": "s-host", "ssh": "s-host"}]


def test_fresh_lives_local_40s_old_syncs_once_in_process(no_ssh, sync_live_calls, live_sync_spawns):
    write_live("loc", live_doc("loc", NOW - 40))
    out = cli.fresh_lives(cfg_with(hosts=[HOSTS[0]]), NOW)
    assert sync_live_calls == ["loc"]
    assert live_sync_spawns == []
    assert [l["host"] for l in out] == ["loc"]


def test_fresh_lives_local_10s_old_does_not_sync(no_ssh, sync_live_calls, live_sync_spawns):
    write_live("loc", live_doc("loc", NOW - 10))
    out = cli.fresh_lives(cfg_with(hosts=[HOSTS[0]]), NOW)
    assert sync_live_calls == [] and live_sync_spawns == []
    assert [l["host"] for l in out] == ["loc"]


def test_fresh_lives_returns_the_refreshed_local_data(no_ssh, monkeypatch):
    write_live("loc", live_doc("loc", NOW - 40))

    def fake(host, cfg, now=None, memo=None):
        write_live(host["name"], live_doc(host["name"], NOW, [sess("/z", host="loc")]))
        return {"host": host["name"], "ok": True, "error": None}
    monkeypatch.setattr(sync, "sync_live", fake)
    out = cli.fresh_lives(cfg_with(hosts=[HOSTS[0]]), NOW)
    assert [s["project"] for s in out[0]["sessions"]] == ["/z"]


def test_fresh_lives_remote_3min_old_spawns_background_once(no_ssh, sync_live_calls, live_sync_spawns):
    write_live("s-host", live_doc("s-host", NOW - 180, [sess("/r", host="s-host")]))
    out = cli.fresh_lives(cfg_with(hosts=[HOSTS[1]]), NOW)
    assert live_sync_spawns == [1]
    assert sync_live_calls == [], "a remote host is never synced in-process"
    assert [l["host"] for l in out] == ["s-host"]   # stale data is still returned, not waited for


def test_fresh_lives_remote_60s_old_does_not_spawn(no_ssh, sync_live_calls, live_sync_spawns):
    write_live("s-host", live_doc("s-host", NOW - 60))
    cli.fresh_lives(cfg_with(hosts=[HOSTS[1]]), NOW)
    assert live_sync_spawns == [] and sync_live_calls == []


def test_fresh_lives_mixed_hosts(no_ssh, sync_live_calls, live_sync_spawns):
    write_live("loc", live_doc("loc", NOW - 40))
    write_live("s-host", live_doc("s-host", NOW - 180))
    out = cli.fresh_lives(cfg_with(hosts=HOSTS), NOW)
    assert sync_live_calls == ["loc"] and live_sync_spawns == [1]
    assert sorted(l["host"] for l in out) == ["loc", "s-host"]


@pytest.fixture
def real_cli(monkeypatch):
    """A second copy of the cli module, so the real background launcher (replaced in the shared
    module by the autouse stub) runs; its Popen is a recorder, never a process."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("projdeck._cli_copy_for_test", cli.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    popens = []

    class FakePopen:
        def __init__(self, argv, *a, **k):
            popens.append(list(argv) if not isinstance(argv, str) else argv)
            self.pid = 1

        def wait(self, *a, **k):
            raise AssertionError("must not wait for the background sync")
    monkeypatch.setattr(mod.subprocess, "Popen", FakePopen)
    mod.popens = popens
    return mod


def _flat(argv):
    return " ".join(argv) if not isinstance(argv, str) else argv


def test_real_launcher_starts_proj_sync_live_quiet_without_waiting(no_ssh, real_cli):
    write_live("s-host", live_doc("s-host", NOW - 180))
    real_cli.fresh_lives(cfg_with(hosts=[HOSTS[1]]), NOW)
    assert len(real_cli.popens) == 1
    flat = _flat(real_cli.popens[0])
    assert "sync" in flat and "--live" in flat and "--quiet" in flat


def test_fresh_lives_marker_within_5min_blocks_spawn(no_ssh, real_cli):
    write_live("s-host", live_doc("s-host", NOW - 180))
    m = sync.marker_path(sync.LIVE_MARKER)
    os.makedirs(os.path.dirname(m), exist_ok=True)
    open(m, "w").close()
    os.utime(m, (time.time() - 60, time.time() - 60))
    real_cli.fresh_lives(cfg_with(hosts=[HOSTS[1]]), NOW)
    assert real_cli.popens == []


def test_fresh_lives_marker_older_than_5min_allows_spawn(no_ssh, real_cli):
    write_live("s-host", live_doc("s-host", NOW - 180))
    m = sync.marker_path(sync.LIVE_MARKER)
    os.makedirs(os.path.dirname(m), exist_ok=True)
    open(m, "w").close()
    os.utime(m, (time.time() - 400, time.time() - 400))
    real_cli.fresh_lives(cfg_with(hosts=[HOSTS[1]]), NOW)
    assert len(real_cli.popens) == 1


def test_marker_name_and_window_are_the_documented_ones():
    assert os.path.basename(sync.marker_path(sync.LIVE_MARKER)) == ".syncing-live"
    assert sync.MARKER_SECONDS == 300


# ---------------------------------------------------------------- ruling 7: one marker logic
def test_cli_defers_to_sync_live_sync_running(no_ssh, monkeypatch, real_cli):
    write_live("s-host", live_doc("s-host", NOW - 180))
    monkeypatch.setattr(sync, "live_sync_running", lambda *a, **k: True)
    real_cli.fresh_lives(cfg_with(hosts=[HOSTS[1]]), NOW)
    assert real_cli.popens == []
    monkeypatch.setattr(sync, "live_sync_running", lambda *a, **k: False)
    real_cli.fresh_lives(cfg_with(hosts=[HOSTS[1]]), NOW)
    assert len(real_cli.popens) == 1


# ---------------------------------------------------------------- AC4 / ruling 5: previews
BACKLOG = {"path": "docs/backlog.md", "mtime": NOW - 50, "open": 2, "done": 0, "raw": None,
           "items": [{"title": "Alpha", "done": False, "section": "Sec", "text": "- **Alpha** ALPHA-BODY"},
                     {"title": "Beta", "done": False, "section": "Sec",
                      "text": "BETA-BODY line one\nline two"}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    (tmp_path / "empty-root").mkdir()
    cfgp.write_text(json.dumps({"hosts": [{"name": "local", "local": True, "roots": [str(tmp_path / "empty-root")]}]}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfgp))
    write_cache("local", [{"path": "/proj/a", "last_mtime": NOW - 100, "backlog": BACKLOG},
                          {"path": "/proj/raw", "last_mtime": NOW - 100,
                           "backlog": {"path": "docs/backlog.md", "mtime": NOW, "items": [],
                                       "open": 0, "done": 0, "raw": "RAW-FREE-TEXT"}}])


def run(capsys, *argv):
    assert cli.main(list(argv)) == 0
    return ui.strip_ansi(capsys.readouterr().out)


def test_preview_includes_live_recent_6h_section(env, capsys):
    s = sess("/proj/a", host="local", last_reply="UNIQUE-LAST-WORDS",
             segments=[["busy", NOW - 3000, NOW - 600]])
    write_live("local", live_doc("local", NOW - 5, [s]))
    out = run(capsys, "_preview", "local", "/proj/a")
    assert "最近 6 小时" in out and "UNIQUE-LAST-WORDS" in out


def test_preview_without_live_has_no_live_section(env, capsys):
    out = run(capsys, "_preview", "local", "/proj/a")
    assert "local:/proj/a" in out and "最近 6 小时" not in out


def test_preview_uncached_project_with_live_session_uses_minimal_row(env, capsys):
    s = sess("/proj/brandnew", host="local", segments=[["busy", NOW - 3000, NOW - 600]])
    write_live("local", live_doc("local", NOW - 5, [s]))
    out = run(capsys, "_preview", "local", "/proj/brandnew")
    assert "(not in cache)" not in out
    assert "最近 6 小时" in out and "/proj/brandnew" in out


def test_preview_uncached_project_without_session_says_not_in_cache(env, capsys):
    write_live("local", live_doc("local", NOW - 5, [sess("/proj/a", host="local")]))
    out = run(capsys, "_preview", "local", "/proj/nothere")
    assert "(not in cache)" in out


def test_bpreview_gives_item_body(env, capsys):
    out = run(capsys, "_bpreview", "local", "/proj/a", "1")
    assert "BETA-BODY line one" in out and "ALPHA-BODY" not in out


def test_bpreview_empty_idx_does_not_crash_and_summarises(env, capsys):
    out = run(capsys, "_bpreview", "local", "/proj/a", "")
    assert "Alpha" in out and "Beta" in out and "BETA-BODY" not in out


def test_bpreview_empty_idx_on_unparsable_backlog_shows_raw(env, capsys):
    out = run(capsys, "_bpreview", "local", "/proj/raw", "")
    assert "RAW-FREE-TEXT" in out


def test_bpreview_delegates_to_ui_with_empty_string(env, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(ui, "backlog_preview_text", lambda row, idx: seen.append(idx) or "X")
    run(capsys, "_bpreview", "local", "/proj/a", "")
    assert seen == [""]


# ---------------------------------------------------------------- AC4: README
def test_readme_key_table_has_new_keys():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(here, "README.md"), encoding="utf-8").read()
    assert "^L backlog" in text and "^V dashboard" in text
