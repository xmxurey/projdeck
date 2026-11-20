"""board.host_freshness: one verdict on how current a host's live data is (local hosts included)."""
from projdeck import board, cli, serve
from tests.test_board_unknown import CFG, NOW, P, cache, live, project, sess

REMOTE_CFG = {"hosts": [{"name": "s-host", "local": True, "mux": "zellij"},
                        {"name": "em-host", "ssh": "em-host"}],
              "tiers_days": {"active": 14, "stale": 90}}


def local_board(age, **kw):
    lv = live("s-host", [sess("b", "busy", status="busy")], at=NOW - age, synced_at=NOW - age, **kw)
    return board.build([cache("s-host", [project()])], [lv], CFG, NOW)


def test_stale_local_host_leaves_every_exit():
    b = local_board(7200, stale_since=NOW - 7000, last_error="boom")
    assert b["busy"] == [] and b["waiting"] == []
    assert [s["id"] for s in b["stale_host_sessions"]] == ["b"]
    assert b["stale_host_sessions"][0]["age"] == 7200
    assert [t["state"] for t in b["tiles"] if t["project"] == P] == ["unknown"]
    assert board.statusline_doc(b, NOW)["waiting"] == []


def test_local_levels_by_age():
    for age, level, busy in ((10, "fresh", 1), (90, "aging", 1), (130, "stale", 0)):
        b = local_board(age)
        assert b["hosts"][0]["freshness"] == {"level": level, "age": age}
        assert len(b["busy"]) == busy


def test_remote_clock_ahead_uses_local_synced_at():
    def lv(synced):
        return live("em-host", [], at=NOW + 900, synced_at=synced)
    h = lambda synced: board.build([cache("em-host")], [lv(synced)], REMOTE_CFG, NOW)["hosts"][-1]
    assert h(NOW - 10)["freshness"]["level"] == "fresh"
    assert h(NOW - 700)["freshness"]["level"] == "stale"


def test_remote_thresholds_differ_from_local():
    h = {"local": False, "live_unknown": False, "live_synced_at": NOW - 200}
    assert board.host_freshness(h, NOW)["level"] == "fresh"
    assert board.host_freshness(dict(h, local=True), NOW)["level"] == "stale"


def test_unknown_only_when_live_unknown():
    b = board.build([cache("s-host")], [live("s-host", [], source="none")], CFG, NOW)
    assert b["hosts"][0]["freshness"] == {"level": "unknown", "age": None}
    lv = live("s-host", [], errors=["/home/u/alpha: git: boom"])
    b = board.build([cache("s-host")], [lv], CFG, NOW)
    assert b["hosts"][0]["freshness"]["level"] != "unknown"


def test_thresholds_exceed_refresh_intervals():
    assert board.STALE_SECONDS["local"] > max(cli.LOCAL_LIVE_SECONDS, serve.LOCAL_EVERY)
    assert board.STALE_SECONDS["remote"] > max(serve.REMOTE_EVERY, cli.REMOTE_LIVE_SECONDS)
    assert board.LIVE_LABEL_SECONDS == 30 and not hasattr(board, "HOST_STALE_SECONDS")
