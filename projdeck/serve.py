"""`proj serve`: a read-only dashboard on the Tailscale address (or loopback).

Security boundary: binds only to 100.64.0.0/10 or loopback (never 0.0.0.0), answers only
GET/HEAD, only for Host headers naming this node, from a fixed route table (no URL ever maps
to a file path), and sends the webkit security headers on every response. Request threads
only read the current snapshot through snapshot_fn; they never collect anything: a single
Refresher thread does, and swaps in each new snapshot under a lock.
"""
import errno
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from projdeck import board, config, sync, termtext, web
from projdeck.vendor.webkit.webkit import SECURITY_HEADERS, check_bind, host_allowed

TAILSCALE_TIMEOUT = 5
REQUEST_TIMEOUT = 30  # seconds a connection may take to send its request head (total)
MAX_CONNECTIONS = 32  # connections handled at once; more are closed unanswered

_HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = {
    "/static/base.css": (os.path.join(_HERE, "vendor", "webkit", "base.css"), "text/css"),
    "/static/poll.js": (os.path.join(_HERE, "vendor", "webkit", "poll.js"), "text/javascript"),
    "/static/app.css": (os.path.join(_HERE, "web", "app.css"), "text/css"),
}
PAGES = {"/": ("home", False), "/projects": ("projects", False),
         "/fragment/home": ("home", True), "/fragment/projects": ("projects", True)}
STATE = "/api/state.json"
HIDDEN_KEYS = ("prompts",)  # never leaves the process, even if a board carries it
PROBE = object()  # "ask tailscale_self()": a ts_self argument nobody has fetched yet


def tailscale_self():
    """This node per `tailscale status --json`: {"ip", "dns_name", "host_name"}, or None
    when tailscale is missing, offline, slow or reports no IPv4 address."""
    try:
        r = subprocess.run(["tailscale", "status", "--json"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           timeout=TAILSCALE_TIMEOUT)
        if r.returncode != 0:
            return None
        me = json.loads(r.stdout).get("Self")
        ip = next(a for a in me.get("TailscaleIPs") or () if isinstance(a, str) and "." in a)
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError, StopIteration):
        return None
    dns = me.get("DNSName")
    host = me.get("HostName")
    return {"ip": ip, "dns_name": dns.rstrip(".") if isinstance(dns, str) and dns else None,
            "host_name": host if isinstance(host, str) and host else None}


LOCAL_EVERY = 15     # seconds between local live refreshes
POKE_EVERY = 5       # ... at most this often while requests come in
REMOTE_EVERY = 120   # remote live, whether or not anyone is looking (D11)
HEARTBEAT = 60       # re-push an unchanged statusline doc this often (D9)
LOOP_MAX_WAIT = 1.0  # run() notices its stop event at least this often
# the remote side mirrors sync.cache_dir()'s default; $$ keeps concurrent pushes apart
PUSH_CMD = ("umask 077; mkdir -p ~/.cache/proj/live && cat > ~/.cache/proj/live/statusline.state.$$.tmp"
            " && mv ~/.cache/proj/live/statusline.state.$$.tmp ~/.cache/proj/live/statusline.state")


def _collect_all(cfg):
    """serve's collect job: the collect layer only; live has its own threads, and running
    sync_live here too would race them on the same host's live cache."""
    return sync.sync_all(cfg, collect_only=True)


def _log(msg):
    print("proj serve: %s" % termtext.one_line(msg), file=sys.stderr)


def statusline_path():
    return os.path.join(sync.cache_dir(), "live", "statusline.state")


def _push_one(host, body, timeout):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                            host["ssh"], PUSH_CMD], input=body, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=timeout)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", "replace").strip() or "exit %d" % r.returncode
            _log("push statusline to %s failed: %s" % (host["name"], err[-300:]))
    except subprocess.TimeoutExpired:
        _log("push statusline to %s failed: timed out after %ss" % (host["name"], timeout))
    except OSError as e:
        _log("push statusline to %s failed: %s" % (host["name"], e))


class StatuslinePusher:
    """Pushes statusline docs to every non-local host with an ssh alias. Per host at most
    one ssh is in flight (bounded by ssh_timeout); docs arriving meanwhile only replace the
    pending one, which goes out as soon as the running push ends, so an older doc can
    never land after a newer one and no change is dropped. Failures are only logged."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._lanes = {}  # host name -> {"inflight": Thread | None, "pending": bytes | None}

    def push(self, doc):
        """Queue doc for every target host; returns the threads it had to start."""
        body = json.dumps(doc).encode("utf-8")
        started = []
        for h in self.cfg["hosts"]:
            if h.get("local") or not h.get("ssh"):
                continue
            with self._lock:
                lane = self._lanes.setdefault(h["name"], {"inflight": None, "pending": None})
                lane["pending"] = body
                if lane["inflight"] is None:
                    t = threading.Thread(target=self._drain, args=(h, lane),
                                         name="proj-push-%s" % h["name"], daemon=True)
                    lane["inflight"] = t
                    try:
                        t.start()
                    except Exception as e:  # e.g. RuntimeError: no thread left
                        lane["inflight"] = None  # the next push tries again
                        _log("push statusline to %s failed: cannot start a thread: %s"
                             % (h["name"], e))
                        continue
                    started.append(t)
        return started

    def _drain(self, host, lane):
        """Send the pending doc until none is left. Whatever goes wrong, the lane is freed
        on the way out, or this host would never be pushed to again."""
        try:
            while True:
                with self._lock:
                    body, lane["pending"] = lane["pending"], None
                    if body is None:
                        lane["inflight"] = None
                        return
                _push_one(host, body, self.cfg["ssh_timeout"])
        except Exception as e:
            _log("push statusline to %s failed: %r" % (host["name"], e))
        finally:
            with self._lock:
                if lane["inflight"] is threading.current_thread():
                    lane["inflight"] = None


class Refresher:
    """What `proj serve` serves. tick(now) runs whatever is due: local live every 15 s (or
    5 s after a poke) in this thread, with the memo only this thread touches; remote live
    every 120 s and collect every auto_sync_minutes in background threads, one per job,
    never two of the same at once. Each local refresh rebuilds the board from the caches,
    swaps it in under the lock, writes live/statusline.state and pushes it when the
    waiting list changed or HEARTBEAT seconds passed."""

    def __init__(self, cfg, clock=time.time, sync_live_fn=sync.sync_live,
                 sync_all_fn=_collect_all, push_fn=None):
        self.cfg = cfg
        self.clock = clock
        self.sync_live_fn = sync_live_fn
        self.sync_all_fn = sync_all_fn
        self.push_fn = push_fn or StatuslinePusher(cfg).push
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._poked = False
        self._memo = {}
        self._workers = {}  # job name -> Thread
        self._last_local = self._last_remote = None
        self._last_collect = sync.oldest_sync(cfg)  # None: collect on the first tick
        self._pushed_waiting = self._pushed_at = None
        self._snap = board.build(sync.load_caches(cfg), sync.load_lives(cfg), cfg, clock())

    def snapshot(self):
        with self._lock:
            return self._snap

    def poke(self):
        """A request came in: refresh local live as soon as POKE_EVERY allows."""
        with self._lock:
            self._poked = True
        self._wake.set()

    def _local_due(self):
        """When local live is next due: at once before the first refresh."""
        if self._last_local is None:
            return 0
        with self._lock:
            poked = self._poked
        return self._last_local + (POKE_EVERY if poked else LOCAL_EVERY)

    def _next_due(self):
        due = [self._local_due()]
        if self._last_remote is not None:
            due.append(self._last_remote + REMOTE_EVERY)
        if self._last_collect is not None:
            due.append(self._last_collect + self.cfg["auto_sync_minutes"] * 60)
        return min(due)

    def tick(self, now):
        """Local refresh first, and each step on its own: one failing step (a bad config
        value, a thread that cannot start) must not stop the others."""
        for what, step in (("local refresh", self._tick_local), ("remote live", self._tick_remote),
                           ("collect", self._tick_collect)):
            try:
                step(now)
            except Exception as e:
                _log("%s failed: %r" % (what, e))

    def _tick_local(self, now):
        if now >= self._local_due():
            self._refresh_local(now)

    def _tick_remote(self, now):
        if self._last_remote is None or now - self._last_remote >= REMOTE_EVERY:
            self._last_remote = now
            for h in self.cfg["hosts"]:
                if not h.get("local"):
                    self._spawn("live " + h["name"], self._remote_live, h, now)

    def _tick_collect(self, now):
        if (self._last_collect is None
                or now - self._last_collect >= self.cfg["auto_sync_minutes"] * 60):
            self._last_collect = now
            self._spawn("collect", self._collect)

    def _spawn(self, job, fn, *args):
        with self._lock:
            old = self._workers.get(job)
        if old and old.is_alive():
            return  # still stuck in the last round (ssh_timeout bounds it)
        t = threading.Thread(target=fn, args=args, name="proj-" + job, daemon=True)
        t.start()
        with self._lock:
            self._workers[job] = t

    def _report(self, what, result):
        if result and not result.get("ok", True):
            _log("%s %s failed: %s" % (what, result.get("host"), result.get("error")))

    def _remote_live(self, host, now):
        try:
            self._report("live", self.sync_live_fn(host, self.cfg, now))
        except Exception as e:
            _log("live %s failed: %r" % (host["name"], e))

    def _collect(self):
        try:
            for r in self.sync_all_fn(self.cfg) or ():
                self._report("sync", r)
        except Exception as e:
            _log("sync failed: %r" % (e,))

    def _refresh_local(self, now):
        self._last_local = now
        with self._lock:
            self._poked = False
        for h in self.cfg["hosts"]:
            if h.get("local"):
                try:
                    self._report("live", self.sync_live_fn(h, self.cfg, now, self._memo))
                except Exception as e:
                    _log("live %s failed: %r" % (h["name"], e))
        try:
            snap = board.build(sync.load_caches(self.cfg), sync.load_lives(self.cfg),
                               self.cfg, now)
            doc = board.statusline_doc(snap, now)
        except Exception as e:
            _log("building the board failed: %r" % (e,))
            return
        with self._lock:
            self._snap = snap
        try:
            sync._write_json(statusline_path(), doc)
        except OSError as e:
            _log("writing %s failed: %s" % (statusline_path(), e))
        self._maybe_push(doc, now)

    def _maybe_push(self, doc, now):
        if not (self.cfg.get("serve") or {}).get("push_statusline", True):
            return
        if (doc["waiting"] == self._pushed_waiting and self._pushed_at is not None
                and now - self._pushed_at < HEARTBEAT):
            return
        self._pushed_waiting, self._pushed_at = doc["waiting"], now
        try:
            self.push_fn(doc)
        except Exception as e:
            _log("push statusline failed: %s" % (e,))

    def wait_idle(self, timeout):
        """Wait for the background jobs started so far; True if all ended within timeout."""
        deadline = time.monotonic() + timeout
        with self._lock:
            workers = list(self._workers.values())
        for t in workers:
            t.join(max(0.0, deadline - time.monotonic()))
        return not any(t.is_alive() for t in workers)

    def run(self, stop):
        """The refresh thread: tick until stop (an Event) is set."""
        while not stop.is_set():
            self._wake.clear()
            try:
                self.tick(self.clock())
                wait = min(LOOP_MAX_WAIT, max(0.0, self._next_due() - self.clock()))
            except Exception as e:  # keep serving the last good snapshot, try again soon
                _log("refresh failed: %r" % (e,))
                wait = LOOP_MAX_WAIT
            self._wake.wait(wait)


def _bracket(name):
    return "[%s]" % name if ":" in name else name


def _host_names(bind, ts_self):
    names = [bind]
    if ts_self:
        names += [n.rstrip(".") for n in (ts_self.get("dns_name"), ts_self.get("host_name"))
                  if isinstance(n, str) and n.rstrip(".")]
    return names


def allowed_hosts(bind, port, ts_self):
    """Host header values this server answers: the bind IP, the node's MagicDNS name and
    its Tailscale host name, each bare or with ":port". Without ts_self only the IP."""
    out = set()
    for n in _host_names(bind, ts_self):
        out.update({n, _bracket(n), "%s:%d" % (_bracket(n), port)})
    return out


def resolve_bind(cfg, cli_bind, ts_self=PROBE):
    """--bind, else serve.bind, else this node's Tailscale IPv4 (from ts_self when the
    caller already has it); ValueError if none is found or the address is neither
    loopback nor in 100.64.0.0/10."""
    addr = cli_bind or (cfg.get("serve") or {}).get("bind")
    if not addr:
        ts = tailscale_self() if ts_self is PROBE else ts_self
        addr = ts and ts.get("ip")
        if not addr:
            raise ValueError("no bind address: give --bind or serve.bind, or bring tailscale "
                             "up (`tailscale status --json` gave no IPv4 address)")
    check_bind(addr)
    return addr


def resolve_port(cfg, cli_port):
    """--port, else serve.port, else 8787; ValueError unless it is in 1-65535."""
    if cli_port is not None:
        port, what = cli_port, "--port"
    else:
        port, what = (cfg.get("serve") or {}).get("port", 8787), '"serve.port"'
    if not config.valid_port(port):
        raise ValueError("%s must be an integer in 1-65535, got %r" % (what, port))
    return port


def _public(obj):
    if isinstance(obj, dict):
        return {k: _public(v) for k, v in obj.items() if k not in HIDDEN_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_public(v) for v in obj]
    return obj


def _read(path):
    with open(path, "rb") as f:
        return f.read()


class _DeadlineReader:
    """rfile for a request handler: reads straight from the socket, but the whole read
    phase shares one deadline, so a client dribbling a byte at a time cannot hold the
    connection. Past the deadline it raises socket.timeout, which the handler treats as
    a dead connection. close() does not close the socket (the handler owns it)."""

    def __init__(self, sock, deadline):
        self._sock = sock
        self._deadline = deadline
        self._buf = b""

    def _fill(self):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("timed out")
        self._sock.settimeout(min(remaining, REQUEST_TIMEOUT))
        data = self._sock.recv(8192)
        self._buf += data
        return bool(data)

    def readline(self, limit=-1):
        while b"\n" not in self._buf and (limit < 0 or len(self._buf) < limit):
            if not self._fill():
                break
        end = self._buf.find(b"\n") + 1 or len(self._buf)
        if limit >= 0:
            end = min(end, limit)
        line, self._buf = self._buf[:end], self._buf[end:]
        return line

    def read(self, n=-1):
        while (n < 0 or len(self._buf) < n) and self._fill():
            pass
        end = len(self._buf) if n < 0 else n
        data, self._buf = self._buf[:end], self._buf[end:]
        return data

    def close(self):
        pass


class _Bounded:
    """Mixin for a ThreadingHTTPServer that handles at most MAX_CONNECTIONS connections at once. A
    connection over the limit is closed without a reply: writing one could block on a
    slow reader, which is the very thing the limit guards against."""

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:  # the thread never started: nobody else will release
            self._slots.release()
            raise

    def handle_error(self, request, client_address):
        """SIGTERM (_Terminated) can unwind process_request just after a handler thread
        started: socketserver then closes the request socket under that thread, which fails
        with EBADF. Nothing else closes a connection early, so that one error is shutdown
        noise, not a fault worth a traceback in the journal. Only once SIGTERM has fired: an
        EBADF at any other time is a bug and keeps its traceback."""
        exc = sys.exc_info()[1]
        if _sigterm_received.is_set() and isinstance(exc, OSError) and exc.errno == errno.EBADF:
            return
        super().handle_error(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def _handler(snapshot_fn, static, names, port):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.timeout = REQUEST_TIMEOUT
            super().setup()
            self.rfile.close()
            self.rfile = _DeadlineReader(self.connection, time.monotonic() + REQUEST_TIMEOUT)

        def parse_request(self):
            try:
                return super().parse_request()
            finally:
                # the head is read: rfile and wfile share the socket, and what is left of
                # the read deadline must not become the timeout for writing the response
                self.connection.settimeout(REQUEST_TIMEOUT)

        def version_string(self):
            return "proj"  # no Python version in the Server header

        def log_message(self, fmt, *args):
            pass  # no per-request log

        def end_headers(self):
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            super().end_headers()

        def _reply(self, code, ctype, body, extra=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _text(self, code, msg, extra=()):
            self._reply(code, "text/plain", (msg + "\n").encode(), extra)

        def __getattr__(self, name):
            # every method other than GET/HEAD (do_POST, do_PUT, do_FOO, ...) lands here
            if name.startswith("do_"):
                return lambda: self._text(405, "method not allowed", [("Allow", "GET, HEAD")])
            raise AttributeError(name)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            hosts = self.headers.get_all("Host") or []
            if len(hosts) != 1 or not host_allowed(hosts[0], names, port):
                return self._text(403, "forbidden host")
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            if path in static:
                return self._reply(200, static[path][1], static[path][0])
            if path not in PAGES and path != STATE:
                return self._text(404, "not found")
            try:
                snap = snapshot_fn()
                if path == STATE:
                    ctype = "application/json"
                    body = termtext.no_surrogates(
                        json.dumps(_public(snap), ensure_ascii=False)).encode("utf-8")
                else:
                    ctype = "text/html"
                    page, fragment = PAGES[path]
                    # ages count from now: a snapshot that stopped refreshing reads as old
                    body = termtext.no_surrogates(
                        web.render(snap, page, fragment, now=time.time())).encode("utf-8")
            except Exception as e:  # a render bug must not kill the connection silently
                print("proj serve: %s failed: %r" % (path, e), file=sys.stderr)
                return self._text(500, "internal error")
            self._reply(200, ctype, body)
    return Handler


def _warn_partial_whitelist(bind, port, ts_self):
    """The whitelist is fixed for the process lifetime: say so when tailscale gave no
    names, or requests by name get 403 with nothing in the journal to explain why."""
    missing = [label for key, label in (("dns_name", "MagicDNS name"),
                                        ("host_name", "Tailscale host name"))
               if not (ts_self or {}).get(key)]
    if not missing:
        return
    missing = " or ".join(missing)
    why = ("tailscale reported no %s" % missing if ts_self else "`tailscale status --json` "
           "gave no node info (tailscale not installed, not up or still starting)")
    print("proj serve: Host whitelist is only %s (bare or :%d); requests by %s get 403 "
          "because %s; restart proj serve once tailscale is up"
          % (", ".join(_host_names(bind, ts_self)), port, missing, why), file=sys.stderr)


def make_server(cfg, bind, port, snapshot_fn, ts_self=PROBE):
    """A bound (not yet serving) ThreadingHTTPServer. snapshot_fn() -> board is the only
    thing request threads call. ts_self is what tailscale_self() returned, if the caller
    already asked. Raises ValueError for a bad bind address, OSError when the port cannot
    be bound (port 0 picks a free one)."""
    check_bind(bind)
    if ts_self is PROBE:
        ts_self = tailscale_self()
    static = {p: (_read(f), ctype) for p, (f, ctype) in STATIC.items()}
    # composed here, not at import time, so the IPv6 variant gets the limit too
    attrs = {"address_family": socket.AF_INET6} if ":" in bind else {}
    cls = type("BoundedThreadingHTTPServer", (_Bounded, ThreadingHTTPServer), attrs)
    srv = cls((bind, port), BaseHTTPRequestHandler)
    srv.daemon_threads = True
    actual = srv.server_address[1]
    srv.RequestHandlerClass = _handler(snapshot_fn, static,
                                       allowed_hosts(bind, actual, ts_self), actual)
    _warn_partial_whitelist(bind, actual, ts_self)
    return srv


_sigterm_received = threading.Event()  # set by the SIGTERM handler; serve() clears it on start


class _Terminated(BaseException):
    """SIGTERM (systemd stopping the service), turned into the normal exit path. A
    BaseException like KeyboardInterrupt: socketserver catches and logs every Exception
    raised while it accepts a connection, which would swallow the shutdown."""


def _on_sigterm_exit():
    """Make the first SIGTERM unwind serve() like Ctrl-C does, so its cleanup runs (a
    second one during cleanup is ignored until the old handler is back). Only the main
    thread can set handlers; returns a function that puts the previous one back."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    fired = []

    def raise_terminated(sig, frame):
        if not fired:
            fired.append(sig)
            _sigterm_received.set()  # before the unwind closes sockets under handler threads
            raise _Terminated()
    previous = signal.signal(signal.SIGTERM, raise_terminated)
    return lambda: signal.signal(signal.SIGTERM,
                                 signal.SIG_DFL if previous is None else previous)


def serve(cfg, cli_bind=None, cli_port=None):
    """`proj serve`: runs until interrupted. Exit code 2 (nothing listening) when the
    address or port is unusable."""
    ts_self = tailscale_self()  # once: picks the address and names the Host whitelist
    try:
        bind = resolve_bind(cfg, cli_bind, ts_self)
        port = resolve_port(cfg, cli_port)
    except ValueError as e:
        print("proj serve: %s" % e, file=sys.stderr)
        return 2
    _sigterm_received.clear()  # no flag left over from an earlier serve() in this process
    refresher = Refresher(cfg)  # starts from the existing caches

    def snapshot_fn():
        refresher.poke()
        return refresher.snapshot()
    stop = threading.Event()
    loop = threading.Thread(target=refresher.run, args=(stop,), name="proj-refresh",
                            daemon=True)
    srv = None
    # before the port opens: from the first connection on, SIGTERM must exit cleanly
    restore_sigterm = _on_sigterm_exit()
    try:
        try:
            srv = make_server(cfg, bind, port, snapshot_fn, ts_self)
        except OSError as e:
            print("proj serve: cannot listen on %s port %d: %s" % (bind, port, e),
                  file=sys.stderr)
            return 2
        print("proj serve: http://%s:%d/" % (_bracket(bind), port), file=sys.stderr)
        loop.start()
        srv.serve_forever()
    except (KeyboardInterrupt, _Terminated):
        pass
    finally:
        restore_sigterm()
        if srv is not None:
            srv.server_close()
        stop.set()
        refresher.poke()
        if loop.ident is not None:
            loop.join(10)
        refresher.wait_idle(10)  # let a running sync finish its cache write
    return 0
