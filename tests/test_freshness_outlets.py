"""One freshness verdict at every outlet: terminal header, web host chip, refresh scheduling."""
import re
from pathlib import Path

from projdeck import board, cli, serve, sync, ui, web

NOW = 1_800_000_000
ROOT = Path(__file__).resolve().parent.parent


def host(name="s-host", local=True, age=10, **kw):
    h = {"name": name, "local": local, "live_at": NOW - age, "live_synced_at": NOW - age,
         "collect_at": NOW - 5, "stale_since": None, "error": None, "source": "claude agents",
         "errors": [], "live_unknown": False}
    h.update(kw)
    return h


def header(h):
    return ui.strip_ansi(ui.header_lines({"hosts": [h], "summary": {}}, NOW)[0])


def chip(h):
    return re.sub(r"<[^>]+>", "", web._host_chip(h, NOW))


# ---- terminal header

def test_header_shows_sync_failure_reason():
    assert "⚠ 同步失败：boom" in header(host(error="boom"))


def test_header_failure_reason_cut_to_60_chars():
    out = header(host(error="x" * 80))
    assert "同步失败：" + "x" * 60 in out and "x" * 61 not in out


def test_header_yellow_when_aging_but_not_when_fresh():
    assert "\x1b[33m" not in ui.header_lines({"hosts": [host(age=10)], "summary": {}}, NOW)[0]
    assert "\x1b[33m" in ui.header_lines({"hosts": [host(age=90)], "summary": {}}, NOW)[0]


# ---- web chip

def test_chip_fresh_collect_without_live_is_not_realtime():
    t = chip(host(live_at=None, live_synced_at=None, live_unknown=True, collect_at=NOW - 1))
    assert "活会话还没采集" in t and "实时" not in t


def test_chip_local_20s_is_realtime():
    html = web._host_chip(host(age=20), NOW)
    assert "实时" in html and "dot-ok" in html


def test_chip_local_90s_is_warn():
    html = web._host_chip(host(age=90), NOW)
    assert "dot-warn" in html and "实时" not in html


# ---- scheduling

def test_fresh_lives_uses_own_clock_when_remote_clock_runs_ahead(tmp_path, monkeypatch,
                                                                 live_sync_spawns):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    sync._write_json(sync._live_path("em-host"), {
        "schema": 1, "host": "em-host", "collected_at": NOW + 5000, "synced_at": NOW - 300,
        "stale_since": None, "last_error": None, "sessions": [], "projects": {},
        "zellij_exited": None, "errors": []})
    cli.fresh_lives({"hosts": [{"name": "em-host", "ssh": "em-host"}]}, NOW)
    assert len(live_sync_spawns) == 1


# ---- guards: scheduling constants stay below the verdict thresholds

def test_scheduling_constants_stay_inside_stale_thresholds():
    assert board.STALE_SECONDS["local"] > cli.LOCAL_LIVE_SECONDS
    assert board.STALE_SECONDS["local"] > serve.LOCAL_EVERY
    assert board.STALE_SECONDS["remote"] > serve.REMOTE_EVERY
    assert board.STALE_SECONDS["remote"] > cli.REMOTE_LIVE_SECONDS
    assert board.STALE_SECONDS["remote"] > sync.MARKER_SECONDS


def test_marker_guard_fails_when_marker_window_outgrows_stale(monkeypatch):
    """The guard can tell: a marker window of 700 s must break it."""
    monkeypatch.setattr(sync, "MARKER_SECONDS", 700)
    try:
        test_scheduling_constants_stay_inside_stale_thresholds()
    except AssertionError:
        return
    raise AssertionError("guard did not notice MARKER_SECONDS > remote stale threshold")


def test_no_threshold_duplicates_in_outlets():
    for name in ("ui.py", "web.py"):
        src = (ROOT / "projdeck" / name).read_text()
        assert "HOST_WARN_SECONDS" not in src and "LIVE_STALE_SECONDS" not in src
    assert not hasattr(ui, "HOST_WARN_SECONDS") and not hasattr(ui, "LIVE_STALE_SECONDS")
    chip_src = Path(web.__file__).read_text().split("def _host_chip")[1].split("\ndef ")[0]
    assert not re.search(r"\b(30|300)\b", chip_src)
