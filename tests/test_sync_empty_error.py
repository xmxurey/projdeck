"""F5: a failure whose text is empty still says something, and ^U never crashes on it."""
import json

from projdeck import agent, cli, sync, ui
from tests.test_ui_cli import row

HOST = {"name": "h", "local": True}


def test_record_names_the_exception_when_its_text_is_empty(tmp_path):
    path = str(tmp_path / "c.json")

    def fetch():
        raise ValueError()
    stub = {"schema": agent.SCHEMA, "collected_at": 0}
    r = sync._record(path, fetch, stub, "h", 100)
    assert r == {"host": "h", "ok": False, "error": "ValueError"}
    with open(path) as f:
        assert json.load(f)["last_error"] == "ValueError"


def test_combine_fills_in_an_empty_layer_error():
    c = sync._combine(HOST, {"ok": False, "error": ""}, {"ok": True, "error": None})
    assert c["ok"] is False and c["error"] == "collect: 未知错误"
    c = sync._combine(HOST, None, {"ok": False, "error": None})
    assert c["ok"] is False and c["error"] == "live: 未知错误"
    c = sync._combine(HOST, {"ok": True, "error": None}, {"ok": True, "error": None})
    assert c == {"host": "h", "ok": True, "error": None}


def test_ctrl_u_survives_a_failure_without_error_text(monkeypatch):
    cfg = {"hosts": [{"name": "vm", "ssh": "vm"}], "tiers_days": {"active": 14, "stale": 90}}
    cache = {"host": "vm", "projects": [row(path="/p")]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: [cache])
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: [])
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: [cache])
    monkeypatch.setattr(cli.sync, "sync_all", lambda cfg: [{"host": "vm", "ok": False, "error": None}])
    picks = iter([("ctrl-u", "vm", "/p")])
    notices = []

    def fake_pick(rows, now, notice=None, select=None, board=None):
        notices.append(notice)
        try:
            return next(picks)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", fake_pick)
    assert cli.cmd_browse(cfg, None) == 0
    assert notices[-1] == "sync: vm FAILED"
