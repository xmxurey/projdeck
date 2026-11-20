"""serve's refresh thread: cadence, snapshot swap, statusline.state and the push to remotes."""
import json
import os
import signal
import socket
import threading
import time

import pytest

from projdeck import agent, board, serve, sync

T0 = 1_700_000_000
LOCAL = {"name": "s-host", "local": True}
REMOTE = {"name": "em-host", "ssh": "em-host"}


def make_cfg(hosts=(LOCAL, REMOTE), push=True, minutes=30):
    return {"hosts": [dict(h) for h in hosts], "ssh_timeout": 5, "auto_sync_minutes": minutes,
            "serve": {"bind": None, "port": 8787, "push_statusline": push}}


def session(sid, project, state="waiting", since=T0 - 100):
    return {"id": sid, "project": project, "name": sid + "-name", "state": state,
            "idle_since": since, "started_at": since - 50, "segments": []}


def write_live(name, sessions, now):
    sync._write_json(sync._live_path(name), {
        "schema": agent.SCHEMA, "host": name, "collected_at": now, "sessions": sessions,
        "projects": {}, "synced_at": now, "stale_since": None, "last_error": None})


def state_path():
    return os.path.join(sync.cache_dir(), "live", "statusline.state")


class Fakes:
    """sync_live / sync_all / push stand-ins: record calls, write the live cache from
    `sessions`, and can make one host's "ssh" hang until released."""

    def __init__(self):
        self.sessions = {"s-host": [], "em-host": []}
        self.live_calls = []
        self.all_calls = []
        self.pushed = []
        self.block = {}  # host name -> Event the call waits on
        self.push_error = None

    def sync_live(self, host, cfg, now=None, memo=None):
        self.live_calls.append((host["name"], now, memo))
        ev = self.block.get(host["name"])
        if ev:
            ev.wait(10)
        write_live(host["name"], list(self.sessions[host["name"]]), now)
        return {"host": host["name"], "ok": True, "error": None}

    def sync_all(self, cfg):
        self.all_calls.append(1)
        return []

    def push(self, doc):
        self.pushed.append(json.loads(json.dumps(doc)))
        if self.push_error:
            raise self.push_error

    def calls(self, name):
        return [c for c in self.live_calls if c[0] == name]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    return str(tmp_path / "cache")


def make(cache, cfg=None, fakes=None):
    f = fakes or Fakes()
    r = serve.Refresher(cfg or make_cfg(), lambda: T0, f.sync_live, f.sync_all, f.push)
    return r, f


# ---- acceptance 1: cadence

def test_local_live_every_15s_with_memo(cache):
    r, f = make(cache)
    for dt in (0, 5, 14, 15, 29, 30):
        r.tick(T0 + dt)
    assert [c[1] for c in f.calls("s-host")] == [T0, T0 + 15, T0 + 30]
    memos = [c[2] for c in f.calls("s-host")]
    assert isinstance(memos[0], dict) and all(m is memos[0] for m in memos)


def test_poke_refreshes_early_once_5s_have_passed(cache):
    r, f = make(cache)
    r.tick(T0)
    r.poke()
    r.tick(T0 + 3)  # too soon: the poke waits
    assert len(f.calls("s-host")) == 1
    r.tick(T0 + 5)
    assert [c[1] for c in f.calls("s-host")] == [T0, T0 + 5]
    r.tick(T0 + 11)  # the poke was used up
    assert len(f.calls("s-host")) == 2
    r.tick(T0 + 20)
    assert [c[1] for c in f.calls("s-host")][-1] == T0 + 20


def test_remote_live_every_120s_without_memo(cache):
    r, f = make(cache)
    for dt in (0, 15, 60, 119):
        r.tick(T0 + dt)
        assert r.wait_idle(5)
    assert [(c[1], c[2]) for c in f.calls("em-host")] == [(T0, None)]
    r.tick(T0 + 120)
    assert r.wait_idle(5)
    assert [c[1] for c in f.calls("em-host")] == [T0, T0 + 120]


def test_collect_every_auto_sync_minutes_first_tick_without_caches(cache):
    r, f = make(cache, make_cfg(minutes=30))
    r.tick(T0)
    assert r.wait_idle(5)
    assert len(f.all_calls) == 1
    r.tick(T0 + 29 * 60)
    assert r.wait_idle(5)
    assert len(f.all_calls) == 1
    r.tick(T0 + 30 * 60)
    assert r.wait_idle(5)
    assert len(f.all_calls) == 2


def test_collect_waits_for_fresh_caches(cache):
    for h in ("s-host", "em-host"):
        sync._write_cache(h, {"schema": agent.SCHEMA, "host": h, "projects": [],
                              "synced_at": T0 - 10 * 60})
    r, f = make(cache, make_cfg(minutes=30))
    r.tick(T0)
    assert r.wait_idle(5)
    assert f.all_calls == []
    r.tick(T0 + 20 * 60)
    assert r.wait_idle(5)
    assert len(f.all_calls) == 1


def test_hung_remote_does_not_delay_local_and_is_not_piled_up(cache):
    f = Fakes()
    f.block["em-host"] = threading.Event()
    r, f = make(cache, fakes=f)
    try:
        for dt in (0, 15, 30, 120, 135):
            r.tick(T0 + dt)
        assert [c[1] for c in f.calls("s-host")] == [T0, T0 + 15, T0 + 30, T0 + 120, T0 + 135]
        assert not r.wait_idle(0.05)
        assert len(f.calls("em-host")) == 1  # still running: the 120 s round skipped it
    finally:
        f.block["em-host"].set()
    assert r.wait_idle(5)
    r.tick(T0 + 240)
    assert r.wait_idle(5)
    assert len(f.calls("em-host")) == 2


def test_hung_collect_does_not_delay_local(cache):
    gate = threading.Event()
    f = Fakes()
    f.sync_all = lambda cfg: gate.wait(10)
    r = serve.Refresher(make_cfg(), lambda: T0, f.sync_live, f.sync_all, f.push)
    try:
        r.tick(T0)
        r.tick(T0 + 15)
        assert [c[1] for c in f.calls("s-host")] == [T0, T0 + 15]
        assert not r.wait_idle(0.05)
    finally:
        gate.set()
    assert r.wait_idle(5)


# ---- acceptance 2: statusline.state follows every local refresh

def test_state_file_equals_statusline_doc_of_snapshot(cache):
    r, f = make(cache)
    f.sessions["s-host"] = [session("a", "/w/alpha", since=T0 - 300),
                        session("b", "/w/beta", state="busy")]
    for dt in (0, 15, 30):
        if dt == 30:
            f.sessions["s-host"].append(session("c", "/w/gamma", since=T0 - 900))
        r.tick(T0 + dt)
        with open(state_path()) as fh:
            got = json.load(fh)
        assert got == board.statusline_doc(r.snapshot(), T0 + dt)
        assert got["updated"] == T0 + dt
    assert [w["project"] for w in got["waiting"]] == ["gamma", "alpha"]


def test_remote_data_reaches_snapshot_on_next_local_refresh(cache):
    r, f = make(cache)
    f.sessions["em-host"] = [session("e", "/m/remote-proj")]
    r.tick(T0)
    assert r.wait_idle(5)
    r.tick(T0 + 15)
    assert [s["id"] for s in r.snapshot()["waiting"]] == ["e"]


# ---- acceptance 3: push on change or 60 s heartbeat

def test_push_on_change_and_heartbeat(cache):
    r, f = make(cache)
    f.sessions["s-host"] = [session("a", "/w/alpha")]
    r.tick(T0)
    assert [d["updated"] for d in f.pushed] == [T0]  # first doc goes out at once
    for dt in (15, 30, 45):
        r.tick(T0 + dt)
    assert len(f.pushed) == 1  # only `updated` changed
    r.tick(T0 + 60)
    assert [d["updated"] for d in f.pushed] == [T0, T0 + 60]  # heartbeat
    f.sessions["s-host"].append(session("b", "/w/beta", since=T0 + 50))
    r.tick(T0 + 75)
    assert [d["updated"] for d in f.pushed][-1] == T0 + 75  # changed: same round
    assert [w["session_id"] for w in f.pushed[-1]["waiting"]] == ["a", "b"]
    r.tick(T0 + 90)
    r.tick(T0 + 120)
    assert len(f.pushed) == 3  # heartbeat counts from the last push (75)
    r.tick(T0 + 135)
    assert len(f.pushed) == 4
    with open(state_path()) as fh:
        assert f.pushed[-1] == json.load(fh)


def test_push_disabled_never_pushes(cache):
    r, f = make(cache, make_cfg(push=False))
    f.sessions["s-host"] = [session("a", "/w/alpha")]
    for dt in range(0, 300, 15):
        if dt == 150:
            f.sessions["s-host"] = []
        r.tick(T0 + dt)
    assert f.pushed == []
    assert os.path.exists(state_path())  # the local file is still written


def test_push_failure_is_logged_and_refresh_goes_on(cache, capsys):
    r, f = make(cache)
    f.push_error = OSError("ssh exploded")
    f.sessions["s-host"] = [session("a", "/w/alpha")]
    r.tick(T0)
    f.sessions["s-host"].append(session("b", "/w/beta"))
    r.tick(T0 + 15)
    assert len(f.pushed) == 2
    assert [s["id"] for s in r.snapshot()["waiting"]] == ["a", "b"]
    with open(state_path()) as fh:
        assert json.load(fh)["updated"] == T0 + 15
    err = capsys.readouterr().err
    assert "ssh exploded" in err and len(err.strip().splitlines()) == 2


# ---- acceptance 4: readers always see a whole board

def test_snapshot_readers_never_see_a_partial_board(cache):
    r, f = make(cache)
    stop = threading.Event()
    errors, reads = [], [0]
    keys = set(r.snapshot())

    def reader():
        try:
            while not stop.is_set():
                b = r.snapshot()
                for _ in range(2):  # now, and again after holding the reference a while
                    assert set(b) == keys
                    assert b["summary"]["waiting"] == len(b["waiting"])
                    time.sleep(0.001)
                reads[0] += 1
        except Exception as e:  # noqa: BLE001 - surfaced below
            errors.append(e)
    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    try:
        for i in range(20):
            f.sessions["s-host"] = [session("s%d" % j, "/w/p%d" % j) for j in range(i % 7)]
            r.tick(T0 + 15 * i)
    finally:
        stop.set()
        for t in threads:
            t.join(10)
    assert r.wait_idle(5)
    assert errors == [] and reads[0] > 0
    assert len(r.snapshot()["waiting"]) == 19 % 7


# ---- acceptance 5: cached data is served before the first collection

def test_first_snapshot_comes_from_existing_cache(cache):
    write_live("s-host", [session("old", "/w/cached")], T0 - 30)
    r, f = make(cache)
    assert [s["id"] for s in r.snapshot()["waiting"]] == ["old"]
    assert f.live_calls == [] and f.all_calls == []


# ---- run loop

def test_run_loop_refreshes_and_stops_on_event(cache):
    f = Fakes()
    clock = {"now": T0}
    r = serve.Refresher(make_cfg(), lambda: clock["now"], f.sync_live, f.sync_all, f.push)
    stop = threading.Event()
    t = threading.Thread(target=r.run, args=(stop,))
    t.start()
    try:
        assert r.wait_idle(5)
        for _ in range(200):
            if f.calls("s-host"):
                break
            stop.wait(0.01)
        assert f.calls("s-host")[0][1] == T0
        clock["now"] = T0 + 6
        r.poke()  # wakes the loop: 6 s since the last refresh
        for _ in range(200):
            if len(f.calls("s-host")) > 1:
                break
            stop.wait(0.01)
        assert [c[1] for c in f.calls("s-host")] == [T0, T0 + 6]
    finally:
        stop.set()
        r.poke()
        t.join(5)
    assert not t.is_alive()


# ---- the real push over (fake) ssh

def test_pusher_writes_remote_file_over_ssh(fake_ssh, capsys):
    cfg = make_cfg(hosts=(LOCAL, REMOTE, {"name": "gone", "ssh": "unreachable"}))
    doc = {"updated": T0, "waiting": [{"host": "s-host", "project": "alpha", "name": "n",
                                       "session_id": "a", "since": T0 - 5}]}
    threads = serve.StatuslinePusher(cfg).push(doc)
    assert len(threads) == 2  # the local host is never pushed to
    for t in threads:
        t.join(10)
    remote = os.path.join(os.environ["HOME"], ".cache", "proj", "live", "statusline.state")
    with open(remote) as fh:
        assert json.load(fh) == doc
    assert not [n for n in os.listdir(os.path.dirname(remote)) if n.endswith(".tmp")]
    err = capsys.readouterr().err
    assert "gone" in err and "em-host" not in err


# ---- serve() wires the refresher to the HTTP layer

def test_serve_runs_the_refresher_and_stops_it(tmp_path, monkeypatch):
    from projdeck import cli
    cfg_path = tmp_path / "config.json"
    (tmp_path / "empty-root").mkdir()
    cfg_path.write_text(json.dumps({"hosts": [{"name": "s-host", "local": True, "roots": [str(tmp_path / "empty-root")]}]}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfg_path))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(serve, "tailscale_self", lambda: None)
    seen = {}
    real = serve.ThreadingHTTPServer

    class Probe(real):
        def serve_forever(self, poll_interval=0.5):
            stop = threading.Event()
            for _ in range(500):
                if os.path.exists(state_path()):
                    break
                stop.wait(0.02)
            with open(state_path()) as fh:
                seen["state"] = json.load(fh)
            seen["threads"] = {t.name for t in threading.enumerate()}
            raise KeyboardInterrupt
    monkeypatch.setattr(serve, "ThreadingHTTPServer", Probe)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    assert cli.main(["serve", "--bind", "127.0.0.1", "--port", str(port)]) == 0
    assert seen["state"]["waiting"] == []
    assert "proj-refresh" in seen["threads"]
    assert "proj-refresh" not in {t.name for t in threading.enumerate()}



# ---- review round 1

def test_pushes_to_one_host_are_serialised_and_only_the_latest_waits(tmp_path, monkeypatch):
    """I-1: while a push to a host is in flight, later docs only replace the pending one;
    the remote ends with the newest doc and never sees an older one land last."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log, release = tmp_path / "ssh.log", tmp_path / "release"
    (bindir / "ssh").write_text(
        "#!/bin/sh\n"
        "while [ \"$1\" = -o ]; do shift 2; done\n"
        "shift\n"
        "body=$(cat)\n"
        "printf '%s\\n' \"$body\" >> \"$SSH_LOG\"\n"
        "if [ ! -e \"$SSH_LOG.first\" ]; then : > \"$SSH_LOG.first\"\n"
        "  while [ ! -e \"$SSH_RELEASE\" ]; do sleep 0.02; done; fi\n"
        "printf '%s' \"$body\" | sh -c \"$*\"\n")
    (bindir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("HOME", str(tmp_path / "remote_home"))
    monkeypatch.setenv("SSH_LOG", str(log))
    monkeypatch.setenv("SSH_RELEASE", str(release))
    pusher = serve.StatuslinePusher(make_cfg(hosts=(LOCAL, REMOTE)))
    docs = [{"updated": T0 + i, "waiting": [{"session_id": str(i)}]} for i in range(3)]
    threads = pusher.push(docs[0])
    for _ in range(500):
        if os.path.exists(str(log) + ".first"):
            break
        time.sleep(0.01)
    threads += pusher.push(docs[1]) + pusher.push(docs[2])
    release.write_text("")
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads)
    sent = [json.loads(line) for line in log.read_text().splitlines()]
    assert sent == [docs[0], docs[2]]
    remote = tmp_path / "remote_home" / ".cache" / "proj" / "live" / "statusline.state"
    assert json.loads(remote.read_text()) == docs[2]
    threads = pusher.push(docs[1])  # nothing in flight any more: goes straight out
    for t in threads:
        t.join(10)
    assert len(threads) == 1 and json.loads(remote.read_text()) == docs[1]


def test_run_loop_survives_a_failing_clock(cache, capsys):
    """M-2: an exception anywhere in a loop round is logged; the thread keeps refreshing."""
    f = Fakes()
    state = {"n": 0, "now": T0}

    def clock():
        state["n"] += 1
        if 2 <= state["n"] <= 4:
            raise RuntimeError("clock hiccup")
        return state["now"]
    r = serve.Refresher(make_cfg(), clock, f.sync_live, f.sync_all, f.push)
    stop = threading.Event()
    t = threading.Thread(target=r.run, args=(stop,))
    t.start()
    try:
        for _ in range(500):
            if f.calls("s-host"):
                break
            stop.wait(0.01)
        assert t.is_alive()
        assert f.calls("s-host"), "no refresh after the clock failed"
    finally:
        stop.set()
        r.poke()
        t.join(5)
    assert not t.is_alive()
    assert "clock hiccup" in capsys.readouterr().err


def test_sigterm_takes_the_normal_exit_path(tmp_path, monkeypatch):
    """systemd stops the service with SIGTERM: serve() must still stop the refresher and
    wait for running syncs, then restore the previous handler."""
    from projdeck import cli
    cfg_path = tmp_path / "config.json"
    (tmp_path / "empty-root").mkdir()
    cfg_path.write_text(json.dumps({"hosts": [{"name": "s-host", "local": True, "roots": [str(tmp_path / "empty-root")]}]}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfg_path))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(serve, "tailscale_self", lambda: None)
    seen = {"waited": 0}
    real_wait = serve.Refresher.wait_idle

    def wait_idle(self, timeout):
        seen["waited"] += 1
        return real_wait(self, timeout)
    monkeypatch.setattr(serve.Refresher, "wait_idle", wait_idle)
    caught = []

    def test_handler(sig, frame):  # stands in if serve() installs nothing: no real kill
        caught.append(sig)
    previous = signal.signal(signal.SIGTERM, test_handler)
    real = serve.ThreadingHTTPServer

    class Probe(real):
        def serve_forever(self, poll_interval=0.5):
            os.kill(os.getpid(), signal.SIGTERM)
            for _ in range(100):
                time.sleep(0.02)
            seen["fell_through"] = True
            raise KeyboardInterrupt
    monkeypatch.setattr(serve, "ThreadingHTTPServer", Probe)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    try:
        assert cli.main(["serve", "--bind", "127.0.0.1", "--port", str(port)]) == 0
        assert signal.getsignal(signal.SIGTERM) is test_handler
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert caught == [] and "fell_through" not in seen
    assert seen["waited"] == 1
    assert "proj-refresh" not in {t.name for t in threading.enumerate()}


# ---- review round 2: SIGTERM right after the port opens

def _sigterm_serve(tmp_path, monkeypatch, wrap_make_server=None, server_cls=None):
    """Run `proj serve` in this (main) thread. A guard handler stands in for SIGTERM so a
    regression cannot kill pytest; a watchdog shuts the server down if serve() kept
    running after the signal. Returns (rc, guard_calls, watchdog_fired)."""
    from projdeck import cli
    cfg_path = tmp_path / "config.json"
    (tmp_path / "empty-root").mkdir()
    cfg_path.write_text(json.dumps({"hosts": [{"name": "s-host", "local": True, "roots": [str(tmp_path / "empty-root")]}]}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfg_path))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(serve, "tailscale_self", lambda: None)
    holder, fired, caught = {}, [], []
    real_make = serve.make_server

    def make(*a, **k):
        holder["srv"] = real_make(*a, **k)
        if wrap_make_server:
            wrap_make_server(holder["srv"])
        return holder["srv"]
    monkeypatch.setattr(serve, "make_server", make)
    if server_cls:
        monkeypatch.setattr(serve, "ThreadingHTTPServer", server_cls)

    def watchdog():
        for _ in range(300):
            time.sleep(0.01)
            if "srv" in holder:
                break
        if "srv" not in holder:
            return
        port = holder["srv"].server_address[1]
        try:
            socket.create_connection(("127.0.0.1", port), 1).close()  # like a port probe
        except OSError:
            return
        if not done.wait(3):
            fired.append(1)
            holder["srv"].shutdown()
    done = threading.Event()
    previous = signal.signal(signal.SIGTERM, lambda s, f: caught.append(s))
    w = threading.Thread(target=watchdog, daemon=True)
    w.start()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    try:
        rc = cli.main(["serve", "--bind", "127.0.0.1", "--port", str(port)])
    finally:
        done.set()
        signal.signal(signal.SIGTERM, previous)
        w.join(10)
        if "srv" in holder:
            holder["srv"].server_close()
    return rc, caught, fired


def test_sigterm_while_a_request_is_being_accepted_still_exits(tmp_path, monkeypatch):
    """socketserver swallows Exception raised while it accepts a connection (a port probe
    right after start): the SIGTERM exit must not be one of those."""
    real = serve.ThreadingHTTPServer

    class SignalOnAccept(real):
        def process_request(self, request, client_address):
            os.kill(os.getpid(), signal.SIGTERM)
            for _ in range(50):
                time.sleep(0.01)  # the handler runs here, inside socketserver's try
            super().process_request(request, client_address)
    rc, caught, fired = _sigterm_serve(tmp_path, monkeypatch, server_cls=SignalOnAccept)
    assert (rc, caught, fired) == (0, [], [])


def test_sigterm_between_bind_and_serve_forever_still_exits(tmp_path, monkeypatch):
    """The port is open as soon as make_server returns: SIGTERM from then on must already
    take the normal exit path."""
    def kill_after_bind(srv):
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(50):
            time.sleep(0.01)
    rc, caught, fired = _sigterm_serve(tmp_path, monkeypatch, wrap_make_server=kill_after_bind)
    assert (rc, caught, fired) == (0, [], [])
