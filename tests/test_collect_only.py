"""F12: serve's collect job refreshes only the collect layer; live has its own threads."""
from projdeck import serve, sync
from tests.test_serve_refresh import cache, make_cfg  # noqa: F401 (fixture)


def recorders(monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "sync_host", lambda h, cfg, now=None: calls.append(("collect", h["name"]))
                        or {"host": h["name"], "ok": True, "error": None})
    monkeypatch.setattr(sync, "sync_live", lambda h, cfg, now=None, memo=None: calls.append(("live", h["name"]))
                        or {"host": h["name"], "ok": True, "error": None})
    return calls


def test_sync_all_collect_only_skips_the_live_layer(monkeypatch):
    calls = recorders(monkeypatch)
    out = sync.sync_all(make_cfg(), collect_only=True)
    assert sorted(calls) == [("collect", "em-host"), ("collect", "s-host")]
    assert [r["ok"] for r in out] == [True, True]


def test_sync_all_default_still_does_both_layers(monkeypatch):
    calls = recorders(monkeypatch)
    sync.sync_all(make_cfg(hosts=({"name": "s-host", "local": True},)))
    assert calls == [("collect", "s-host"), ("live", "s-host")]


def test_serve_collect_job_is_collect_only(cache, monkeypatch):
    calls = recorders(monkeypatch)
    r = serve.Refresher(make_cfg(), lambda: 0, push_fn=lambda doc: None)
    r._collect()
    assert sorted(calls) == [("collect", "em-host"), ("collect", "s-host")]
