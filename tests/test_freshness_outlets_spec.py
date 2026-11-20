"""T06 spec tests: terminal header, web host chip and refresh scheduling all follow
board.host_freshness (synced_at on the local clock first, collected_at as fallback)."""
import copy
import json
import os
import re
import subprocess
import time

import pytest

from projdeck import board, cli, config, serve, sync, ui, web

NOW = 1_800_000_000
P = "/home/u/alpha"
CFG = {"hosts": [{"name": "s-host", "local": True, "mux": "zellij"},
                 {"name": "em-host", "ssh": "em-host"}],
       "tiers_days": {"active": 14, "stale": 90}}
YELLOW = re.compile(r"\x1b\[(?:[0-9;]*;)?(?:33|93)m|38;5;(?:3|11|172|178|214|220|221|226|227)m"
                    r"|38;2;(?:2[0-5]\d|1[89]\d);(?:1[5-9]\d|2[0-5]\d);(?:[0-9]|[1-9]\d)m")


def live(host, age=10, now=NOW, collected_age=None, **kw):
    d = {"schema": 1, "host": host, "collected_at": now - (age if collected_age is None else collected_age),
         "synced_at": now - age, "stale_since": None, "last_error": None, "source": "claude agents",
         "sessions": [], "projects": {}, "errors": []}
    d.update(kw)
    return d


def cache(host, age=30):
    return {"schema": 1, "host": host, "collected_at": NOW - age, "synced_at": NOW - age,
            "stale_since": None, "last_error": None,
            "projects": [{"path": P, "exists": True, "last_mtime": NOW - 100, "sessions": [],
                          "git": {"branch": "main", "dirty": 0, "ahead": 0, "no_upstream": False,
                                  "last_commit_ts": NOW - 3600, "last_commit_subject": "s",
                                  "commit_ts_14d": [NOW - 3600]}, "errors": []}]}


def cfg_for(host):
    return {"hosts": [h for h in CFG["hosts"] if h["name"] == host], "tiers_days": CFG["tiers_days"]}


def one(lv, host="s-host"):
    return board.build([cache(host)], [lv] if lv else [], cfg_for(host), NOW)


def remote(lv, c=None):
    return board.build([c or cache("em-host")], [lv] if lv else [], cfg_for("em-host"), NOW)


def chip_html(b, now=NOW):
    return web.render(b, "home", fragment=True, now=now)


# ---------------------------------------------------------------- AC1: terminal header

def header1(b):
    return ui.header_lines(b, NOW)[0]


def head_chip_part(raw):
    cut = len(raw)
    for marker in ("⚠", "◆"):
        if marker in raw:
            cut = min(cut, raw.index(marker))
    return re.sub(r"(?:\x1b\[[0-9;]*m)+$", "", raw[:cut])  # drop the marker's own colour code


def test_error_boom_appended_on_first_line():
    b = one(live("s-host", 10, last_error="boom"))
    assert "⚠ 同步失败：boom" in ui.strip_ansi(header1(b))


def test_no_error_no_failure_marker():
    b = one(live("s-host", 10))
    assert "同步失败" not in ui.strip_ansi(header1(b))


def test_long_error_truncated_to_60():
    b = one(live("s-host", 10, last_error="x" * 100))
    l1 = ui.strip_ansi(header1(b))
    assert "⚠ 同步失败：" + "x" * 60 in l1
    assert "x" * 61 not in l1


def test_error_of_exactly_60_shown_whole():
    b = one(live("s-host", 10, last_error="y" * 60))
    l1 = ui.strip_ansi(header1(b))
    assert "y" * 60 in l1 and "y" * 61 not in l1


def test_short_error_not_padded_or_cut():
    b = one(live("s-host", 10, last_error="ssh: connect failed"))
    assert "⚠ 同步失败：ssh: connect failed" in ui.strip_ansi(header1(b))


def test_fresh_host_without_error_is_not_yellow():
    raw = header1(one(live("s-host", 10)))
    assert not YELLOW.search(head_chip_part(raw)), repr(raw)


def test_aging_local_host_is_yellow():
    b = one(live("s-host", 90))
    assert b["hosts"][0]["freshness"]["level"] == "aging"
    assert YELLOW.search(head_chip_part(header1(b)))


def test_stale_local_host_is_yellow():
    b = one(live("s-host", 400))
    assert YELLOW.search(head_chip_part(header1(b)))


def test_error_alone_makes_a_fresh_host_yellow():
    b = one(live("s-host", 10, last_error="boom"))
    assert b["hosts"][0]["freshness"]["level"] == "fresh"
    assert YELLOW.search(header1(b))


def test_source_none_is_yellow():
    b = one(live("s-host", 10, source="none"))
    assert YELLOW.search(head_chip_part(header1(b)))


def test_remote_200s_old_is_still_fresh_and_quiet():
    # same age is aging for a local host (> 120) but fresh for a remote one (<= 300)
    b = remote(live("em-host", 200))
    assert b["hosts"][0]["freshness"]["level"] == "fresh"
    assert not YELLOW.search(head_chip_part(header1(b)))


def test_header_uses_local_clock_not_remote_collected_at():
    # remote clock far in the future: collected_at is useless, synced_at says fresh
    b = remote(live("em-host", 10, collected_age=-5000))
    assert not YELLOW.search(head_chip_part(header1(b)))


# ---------------------------------------------------------------- AC2: web chip

def test_fresh_collect_but_no_live_says_not_collected_yet():
    b = one(None)
    html = chip_html(b)
    assert "活会话还没采集" in html
    assert "s-host 实时" not in html and "实时" not in html


def test_stale_collect_without_live_still_not_collected_yet():
    b = board.build([cache("s-host", 5000)], [], cfg_for("s-host"), NOW)
    assert "活会话还没采集" in chip_html(b)


def test_collect_time_never_leaks_into_the_chip_when_live_exists():
    # fresh collect, old live: the chip follows the live layer only
    b = board.build([cache("s-host", 5)], [live("s-host", 400)], cfg_for("s-host"), NOW)
    html = chip_html(b)
    assert "s-host 实时" not in html
    assert "活会话还没采集" not in html


def test_local_20s_is_realtime_and_not_warn():
    html = chip_html(one(live("s-host", 20)))
    assert "s-host 实时" in html
    assert "dot-warn" not in html


def test_local_90s_is_warn():
    html = chip_html(one(live("s-host", 90)))
    assert "dot-warn" in html
    assert "s-host 实时" not in html


def test_realtime_boundary_30_vs_31():
    assert "s-host 实时" in chip_html(one(live("s-host", 30)))
    assert "s-host 实时" not in chip_html(one(live("s-host", 31)))


def test_local_age_between_label_and_fresh_is_fresh_but_not_realtime():
    b = one(live("s-host", 45))
    assert b["hosts"][0]["freshness"]["level"] == "fresh"
    html = chip_html(b)
    assert "s-host 实时" not in html and "dot-warn" not in html


def test_chip_age_counts_from_request_time():
    b = one(live("s-host", 10))
    assert "s-host 实时" in chip_html(b, NOW + 5)
    assert "s-host 实时" not in chip_html(b, NOW + 25)  # 35s old at request time


def test_chip_level_follows_request_time():
    b = one(live("s-host", 10))
    assert "dot-warn" not in chip_html(b, NOW)
    assert "dot-warn" in chip_html(b, NOW + 100)  # 110s: aging for a local host


def test_remote_100s_is_fresh_no_warn_no_realtime():
    b = remote(live("em-host", 100))
    html = chip_html(b)
    assert "em-host 实时" not in html and "dot-warn" not in html


def test_chip_ignores_remote_clock_collected_at():
    b = remote(live("em-host", 10, collected_age=-5000))
    assert "em-host 实时" in chip_html(b)


def test_chip_old_collected_at_but_recent_sync_is_realtime():
    b = one(live("s-host", 10, collected_age=3000))
    assert "s-host 实时" in chip_html(b)


# ---------------------------------------------------------------- AC3: fresh_lives

CNOW = int(time.time())


def cfg_with(hosts):
    c = copy.deepcopy(config.DEFAULTS)
    c["hosts"] = hosts
    return c


def write_live(host, doc):
    p = os.path.join(sync.cache_dir(), "live", host + ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(doc, f)


def ldoc(host, collected_at, synced_at="absent"):
    d = {"schema": 1, "host": host, "collected_at": collected_at, "stale_since": None,
         "last_error": None, "sessions": [], "projects": {}, "zellij_exited": None, "errors": []}
    if synced_at != "absent":
        d["synced_at"] = synced_at
    return d


REMOTE = [{"name": "em-host", "ssh": "em-host"}]
LOCAL = [{"name": "s-host", "local": True}]


@pytest.fixture
def no_procs(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("unexpected process / ssh")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)
    monkeypatch.setattr(sync, "run_agent", boom)


@pytest.fixture
def sync_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "sync_live",
                        lambda host, cfg, now=None, memo=None: calls.append(host["name"]) or
                        {"host": host["name"], "ok": True, "error": None})
    return calls


def test_remote_clock_ahead_stale_synced_at_spawns(no_procs, live_sync_spawns):
    write_live("em-host", ldoc("em-host", CNOW + 500, CNOW - 300))
    cli.fresh_lives(cfg_with(REMOTE), CNOW)
    assert live_sync_spawns == [1]


def test_remote_clock_ahead_but_recent_synced_at_does_not_spawn(no_procs, live_sync_spawns):
    write_live("em-host", ldoc("em-host", CNOW + 500, CNOW - 5))
    cli.fresh_lives(cfg_with(REMOTE), CNOW)
    assert live_sync_spawns == []


def test_old_collected_at_but_recent_synced_at_does_not_spawn(no_procs, live_sync_spawns):
    write_live("em-host", ldoc("em-host", CNOW - 5000, CNOW - 5))
    cli.fresh_lives(cfg_with(REMOTE), CNOW)
    assert live_sync_spawns == []


def test_without_synced_at_falls_back_to_collected_at(no_procs, live_sync_spawns):
    write_live("em-host", ldoc("em-host", CNOW - 300))
    cli.fresh_lives(cfg_with(REMOTE), CNOW)
    assert live_sync_spawns == [1]


def test_without_synced_at_recent_collected_at_does_not_spawn(no_procs, live_sync_spawns):
    write_live("em-host", ldoc("em-host", CNOW - 5))
    cli.fresh_lives(cfg_with(REMOTE), CNOW)
    assert live_sync_spawns == []


def test_local_clock_ahead_stale_synced_at_syncs(no_procs, sync_calls, live_sync_spawns):
    write_live("s-host", ldoc("s-host", CNOW + 500, CNOW - 300))
    cli.fresh_lives(cfg_with(LOCAL), CNOW)
    assert sync_calls == ["s-host"]


def test_local_recent_synced_at_does_not_sync(no_procs, sync_calls, live_sync_spawns):
    write_live("s-host", ldoc("s-host", CNOW - 5000, CNOW - 3))
    cli.fresh_lives(cfg_with(LOCAL), CNOW)
    assert sync_calls == [] and live_sync_spawns == []


# ---------------------------------------------------------------- AC4 (attributes) and guards

def test_old_threshold_constants_are_gone():
    assert not hasattr(ui, "HOST_WARN_SECONDS")
    assert not hasattr(ui, "LIVE_STALE_SECONDS")
    assert not hasattr(web, "HOST_WARN_SECONDS")
    assert not hasattr(web, "LIVE_STALE_SECONDS")


def test_guard_stale_thresholds_exceed_schedule_constants():
    assert board.STALE_SECONDS["local"] > cli.LOCAL_LIVE_SECONDS
    assert board.STALE_SECONDS["local"] > serve.LOCAL_EVERY
    assert board.STALE_SECONDS["remote"] > serve.REMOTE_EVERY
    assert board.STALE_SECONDS["remote"] > cli.REMOTE_LIVE_SECONDS


def test_guard_fresh_thresholds_exceed_schedule_constants():
    # a host refreshed on schedule must not look aging between two refreshes
    assert board.FRESH_SECONDS["local"] >= cli.LOCAL_LIVE_SECONDS
    assert board.FRESH_SECONDS["local"] >= serve.LOCAL_EVERY
    assert board.FRESH_SECONDS["remote"] >= serve.REMOTE_EVERY
    assert board.FRESH_SECONDS["remote"] >= cli.REMOTE_LIVE_SECONDS
    assert board.LIVE_LABEL_SECONDS <= board.FRESH_SECONDS["local"]
