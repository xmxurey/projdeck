"""T05 spec tests: one freshness judge (board.host_freshness) for every host, local included."""
import pytest

from projdeck import board, cli, serve

NOW = 1_800_000_000
P = "/home/u/alpha"
CFG = {"hosts": [{"name": "s-host", "local": True, "mux": "zellij"},
                 {"name": "em-host", "ssh": "em-host"}],
       "tiers_days": {"active": 14, "stale": 90}}


def sess(sid, state, **kw):
    s = {"id": sid, "project": P, "kind": "interactive", "live": True, "name": sid, "title": sid,
         "started_at": NOW - 5000, "status": "idle", "state": state, "idle_since": NOW - 1000,
         "question": "", "subagents_running": 0, "segments": [["busy", NOW - 3000, NOW - 2000]],
         "attach": "", "errors": []}
    s.update(kw)
    return s


def live(host, sessions, age=10, now=NOW, **kw):
    d = {"schema": 1, "host": host, "collected_at": now - age, "synced_at": now - age,
         "stale_since": None, "last_error": None, "source": "claude agents",
         "sessions": sessions, "projects": {}, "errors": []}
    d.update(kw)
    return d


def project(path=P):
    return {"path": path, "exists": True, "last_mtime": NOW - 100, "sessions": [],
            "git": {"branch": "main", "dirty": 0, "ahead": 0, "no_upstream": False,
                    "last_commit_ts": NOW - 3600, "last_commit_subject": "s",
                    "commit_ts_14d": [NOW - 3600]}, "errors": []}


def cache(host):
    return {"schema": 1, "host": host, "collected_at": NOW - 100, "synced_at": NOW - 100,
            "stale_since": None, "last_error": None, "projects": [project()]}


def build(lv, host="s-host", now=NOW):
    return board.build([cache(host)], [lv], CFG, now)


def host(b, name):
    return {h["name"]: h for h in b["hosts"]}[name]


def level(lv, name="s-host", now=NOW):
    return host(build(lv, name, now), name)["freshness"]["level"]


def ids(xs):
    return [x["id"] for x in xs]


# ---- constants and guards (criterion 5)

def test_threshold_constants_per_d3():
    assert board.FRESH_SECONDS == {"local": 60, "remote": 300}
    assert board.STALE_SECONDS == {"local": 120, "remote": 600}
    assert board.LIVE_LABEL_SECONDS == 30


def test_old_constant_is_gone():
    assert not hasattr(board, "HOST_STALE_SECONDS")


def test_stale_threshold_exceeds_refresh_cadence():
    assert board.STALE_SECONDS["local"] > cli.LOCAL_LIVE_SECONDS
    assert board.STALE_SECONDS["local"] > serve.LOCAL_EVERY
    assert board.STALE_SECONDS["remote"] > serve.REMOTE_EVERY
    assert board.STALE_SECONDS["remote"] > cli.REMOTE_LIVE_SECONDS


# ---- criterion 2: local levels

@pytest.mark.parametrize("age,want", [(0, "fresh"), (10, "fresh"), (59, "fresh"), (61, "aging"),
                                      (90, "aging"), (119, "aging"), (121, "stale"),
                                      (130, "stale"), (7200, "stale")])
def test_local_levels(age, want):
    assert level(live("s-host", [], age=age)) == want


@pytest.mark.parametrize("age,want", [(60, "fresh"), (120, "aging")])
def test_local_exact_boundary_is_strictly_greater(age, want):
    assert level(live("s-host", [], age=age)) == want


@pytest.mark.parametrize("age,want", [(10, "fresh"), (90, "aging"), (130, "stale")])
def test_local_busy_stays_until_stale(age, want):
    b = build(live("s-host", [sess("b", "busy")], age=age))
    if want == "stale":
        assert b["busy"] == [] and ids(b["stale_host_sessions"]) == ["b"]
    else:
        assert ids(b["busy"]) == ["b"] and not b["stale_host_sessions"]


# ---- remote levels

@pytest.mark.parametrize("age,want", [(10, "fresh"), (299, "fresh"), (301, "aging"),
                                      (599, "aging"), (601, "stale"), (700, "stale")])
def test_remote_levels(age, want):
    assert level(live("em-host", [], age=age), "em-host") == want


@pytest.mark.parametrize("age,want", [(300, "fresh"), (600, "aging")])
def test_remote_exact_boundary_is_strictly_greater(age, want):
    assert level(live("em-host", [], age=age), "em-host") == want


def test_remote_and_local_use_different_thresholds():
    assert level(live("s-host", [], age=200)) == "stale"
    assert level(live("em-host", [], age=200), "em-host") == "fresh"


# ---- synced_at precedence and clock skew (criterion 3)

def test_remote_clock_ahead_but_synced_recently_is_fresh():
    lv = live("em-host", [], age=10, collected_at=NOW + 900)
    assert level(lv, "em-host") == "fresh"


def test_remote_clock_ahead_but_synced_long_ago_is_stale():
    lv = live("em-host", [], age=700, collected_at=NOW + 900)
    assert level(lv, "em-host") == "stale"


def test_synced_at_wins_over_older_collected_at():
    lv = live("s-host", [], age=10, collected_at=NOW - 5000)
    h = host(build(lv), "s-host")
    assert h["freshness"]["level"] == "fresh"
    assert h["live_synced_at"] == NOW - 10


def test_synced_at_wins_over_newer_collected_at():
    lv = live("s-host", [], age=500, collected_at=NOW - 1)
    h = host(build(lv), "s-host")
    assert h["freshness"]["level"] == "stale"
    assert h["live_synced_at"] == NOW - 500


def test_collected_at_used_when_no_synced_at():
    lv = live("s-host", [], age=10)
    del lv["synced_at"]
    lv["collected_at"] = NOW - 500
    h = host(build(lv), "s-host")
    assert h["freshness"]["level"] == "stale"
    assert h["live_synced_at"] == NOW - 500


def test_age_is_now_minus_synced_at():
    h = host(build(live("s-host", [], age=45)), "s-host")
    assert h["freshness"]["age"] == 45


def test_host_freshness_function_agrees_with_build():
    b = build(live("s-host", [], age=90))
    h = host(b, "s-host")
    assert board.host_freshness(h, NOW) == h["freshness"]
    assert board.host_freshness(h, NOW)["level"] == "aging"
    # later "now" on the same host dict ages it
    assert board.host_freshness(h, NOW + 100) ["level"] == "stale"
    assert board.host_freshness(h, NOW + 100)["age"] == 190


def test_levels_are_shift_invariant():
    shift = 12_345_678
    for age in (10, 90, 130):
        a = level(live("s-host", [], age=age))
        b = level(live("s-host", [], age=age, now=NOW + shift), now=NOW + shift)
        assert a == b


# ---- criterion 1: local live gone stale

def stale_board():
    lv = live("s-host", [sess("b", "busy")], age=7200, stale_since=NOW - 7000,
              last_error="boom")
    return build(lv)


def test_stale_local_busy_session_leaves_busy_and_is_listed_with_age():
    b = stale_board()
    assert b["busy"] == []
    assert ids(b["stale_host_sessions"]) == ["b"]
    assert b["stale_host_sessions"][0]["age"] == 7200


def test_stale_local_tile_is_unknown():
    tiles = stale_board()["tiles"]
    assert len(tiles) == 1 and tiles[0]["state"] == "unknown"


def test_stale_local_waiting_leaves_waiting_and_statusline():
    lv = live("s-host", [sess("w", "waiting")], age=7200, stale_since=NOW - 7000,
              last_error="boom")
    b = build(lv)
    assert b["waiting"] == []
    doc = board.statusline_doc(b, NOW)
    assert "w" not in [x["session_id"] for x in doc["waiting"]]
    assert doc["waiting"] == []


def test_stale_local_counts_drop_waiting_and_busy():
    lv = live("s-host", [sess("w", "waiting"), sess("b", "busy")], age=7200)
    b = build(lv)
    assert b["waiting"] == [] and b["busy"] == []


def test_fresh_local_waiting_is_listed_in_statusline():
    b = build(live("s-host", [sess("w", "waiting")], age=10))
    assert ids(b["waiting"]) == ["w"]
    assert board.statusline_doc(b, NOW)["waiting"][0]["session_id"] == "w"
    assert not b["stale_host_sessions"]


def test_aging_local_waiting_still_counts():
    b = build(live("s-host", [sess("w", "waiting")], age=90))
    assert ids(b["waiting"]) == ["w"]


def test_stale_remote_waiting_and_tile():
    b = build(live("em-host", [sess("w", "waiting")], age=700), "em-host")
    assert b["waiting"] == [] and b["busy"] == []
    assert ids(b["stale_host_sessions"]) == ["w"]
    assert b["tiles"][0]["state"] == "unknown"


def test_fresh_tile_is_not_unknown():
    b = build(live("s-host", [sess("w", "waiting")], age=10))
    assert b["tiles"][0]["state"] != "unknown"


def test_hosts_carry_the_new_keys():
    h = host(build(live("s-host", [], age=10)), "s-host")
    assert "live_synced_at" in h and "freshness" in h
    assert set(h["freshness"]) == {"level", "age"}


# ---- criterion 4: live_unknown vs git error (F3)

def test_never_collected_host_is_unknown_with_no_age():
    b = board.build([], [], CFG, NOW)
    f = host(b, "em-host")["freshness"]
    assert f["level"] == "unknown" and f["age"] is None


def test_source_none_host_is_unknown():
    lv = live("s-host", [], source="none", errors=["live agents: x"])
    assert level(lv) == "unknown"


def test_git_error_alone_is_not_unknown():
    lv = live("s-host", [sess("w", "waiting")], errors=["/home/u/beta: git: boom"])
    b = build(lv)
    assert host(b, "s-host")["live_unknown"] is False
    assert host(b, "s-host")["freshness"]["level"] == "fresh"
    assert ids(b["waiting"]) == ["w"]


def test_git_error_on_remote_is_not_unknown():
    lv = live("em-host", [], errors=["/home/u/beta: git: boom"])
    assert level(lv, "em-host") == "fresh"
