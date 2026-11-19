"""F6: one bad push, one failed thread start or one config typo never stalls serve."""
import json
import threading

import pytest

from projdeck import config, serve
from tests.test_serve_refresh import T0, Fakes, cache, make_cfg, write_live  # noqa: F401

REMOTE = {"name": "em", "ssh": "em"}
CFG = {"hosts": [REMOTE], "ssh_timeout": 5}


def join(threads):
    for t in threads:
        t.join(5)
        assert not t.is_alive()


def test_push_lane_recovers_after_an_unexpected_exception(monkeypatch, capsys):
    calls = []

    def push_one(host, body, timeout):
        calls.append(body)
        if len(calls) == 1:
            raise TypeError("timeout must be a number")
    monkeypatch.setattr(serve, "_push_one", push_one)
    p = serve.StatuslinePusher(CFG)
    join(p.push({"n": 1}))
    assert p._lanes["em"]["inflight"] is None
    assert "TypeError" in capsys.readouterr().err
    started = p.push({"n": 2})
    assert len(started) == 1  # the lane is free again
    join(started)
    assert len(calls) == 2


def test_push_lane_recovers_when_the_thread_cannot_start(monkeypatch, capsys):
    real = threading.Thread.start
    fail = [True]

    def start(self):
        if fail[0] and self.name.startswith("proj-push-"):
            raise RuntimeError("can't start new thread")
        return real(self)
    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(serve, "_push_one", lambda host, body, timeout: None)
    p = serve.StatuslinePusher(CFG)
    assert p.push({"n": 1}) == []
    assert p._lanes["em"]["inflight"] is None
    assert "can't start new thread" in capsys.readouterr().err
    fail[0] = False
    join(p.push({"n": 2}))


@pytest.mark.parametrize("key", ["ssh_timeout", "auto_sync_minutes"])
@pytest.mark.parametrize("bad", ["30", True, 0, -5, None, [1]])
def test_config_rejects_bad_timing_values(tmp_path, key, bad):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({key: bad}))
    with pytest.raises(ValueError) as e:
        config.load_config(str(path))
    msg = str(e.value)
    assert str(path) in msg and '"%s"' % key in msg and "positive number" in msg


@pytest.mark.parametrize("good", [1, 2.5, 30])
def test_config_accepts_positive_numbers(tmp_path, good):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ssh_timeout": good, "auto_sync_minutes": good}))
    cfg = config.load_config(str(path))
    assert cfg["ssh_timeout"] == good and cfg["auto_sync_minutes"] == good


def test_tick_refreshes_local_even_when_a_scheduling_step_fails(cache, capsys):
    cfg = make_cfg()
    f = Fakes()
    r = serve.Refresher(cfg, lambda: T0, f.sync_live, f.sync_all, f.push)
    cfg["auto_sync_minutes"] = "30"  # what a config typo did before validation existed
    f.sessions["s-host"] = [{"id": "w", "project": "/home/u/a", "name": "w", "state": "waiting",
                         "idle_since": T0 - 100, "started_at": T0 - 200, "segments": []}]
    r._last_collect = T0 - 10  # due check: T0 - (T0 - 10) >= "30" * 60 raises TypeError
    r.tick(T0)
    assert [c[0] for c in f.calls("s-host")] == ["s-host"]
    assert [s["id"] for s in r.snapshot()["waiting"]] == ["w"]
    assert "TypeError" in capsys.readouterr().err


def test_tick_refreshes_local_before_spawning_background_jobs(cache, monkeypatch):
    order = []
    r = serve.Refresher(make_cfg(), lambda: T0, Fakes().sync_live, Fakes().sync_all,
                        lambda doc: None)
    monkeypatch.setattr(r, "_refresh_local", lambda now: order.append("local"))
    monkeypatch.setattr(r, "_spawn", lambda job, fn, *a: order.append(job))
    r.tick(T0)
    assert order[0] == "local" and "collect" in order and "live em-host" in order
