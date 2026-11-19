"""Run the agent on each host (locally or over ssh) and cache the results."""
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from projdeck import agent
from projdeck.config import collector_request

AGENT_PATH = os.path.abspath(agent.__file__)


class AgentError(Exception):
    pass


def cache_dir():
    return os.path.expanduser(os.environ.get("PROJ_CACHE", "~/.cache/proj"))


def _argv(host, req):
    payload = json.dumps(req)
    if host.get("local"):
        return [sys.executable, AGENT_PATH, payload], None
    with open(AGENT_PATH, "rb") as f:
        script = f.read()
    return (["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host["ssh"],
             "python3 - " + shlex.quote(payload)], script)


def run_agent(host, req, timeout=20):
    argv, stdin = _argv(host, req)
    try:
        r = subprocess.run(argv, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AgentError("timed out after %ss" % timeout)
    except OSError as e:
        raise AgentError(str(e))
    try:
        result = json.loads(r.stdout.decode("utf-8", "replace"))
    except ValueError:
        err = r.stderr.decode("utf-8", "replace").strip() or "no output (exit %d)" % r.returncode
        raise AgentError(err[-300:])
    if not isinstance(result, dict):
        raise AgentError("unexpected agent output: %s" % type(result).__name__)
    if "error" in result:
        raise AgentError(result["error"])
    return result


def _cache_path(name):
    return os.path.join(cache_dir(), name + ".json")


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_cache(name):
    return _read_json(_cache_path(name))


def _write_atomic(path, text):
    """Atomic text write shared by the cache files and the zellij layout: temp name unique
    per pid and thread, then os.replace; the temp file never outlives a failure. Text that
    is not valid unicode (undecoded path bytes) is written back as the original bytes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _write_json(path, data):
    _write_atomic(path, json.dumps(data))


def _write_cache(name, data):
    _write_json(_cache_path(name), data)


SYNC_MARKER = ".syncing"       # exists while a background `proj sync` runs
LIVE_MARKER = ".syncing-live"  # ... and this one while a background `proj sync --live` runs
MARKER_SECONDS = 300           # a marker younger than this means that sync is still running


def marker_path(name):
    return os.path.join(cache_dir(), name)


def marker_fresh(name, now=None):
    now = time.time() if now is None else now
    try:
        return now - os.path.getmtime(marker_path(name)) < MARKER_SECONDS
    except OSError:
        return False


def claim_marker(name, now=None, problems=None):
    """Create the marker atomically; False when another sync holds a fresh one. A marker
    older than MARKER_SECONDS is a leftover: it is removed and claimed again. A permission
    or disk error counts as "not claimed" (False); its text is appended to `problems`."""
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        for _ in range(2):
            try:
                os.close(os.open(marker_path(name), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return True
            except FileExistsError:
                if marker_fresh(name, now):
                    return False
                try:
                    os.remove(marker_path(name))
                except OSError:
                    pass
    except OSError as e:
        if problems is not None:
            problems.append(str(e))
    return False


def release_marker(name):
    try:
        os.remove(marker_path(name))
    except OSError:
        pass


def live_marker_path():
    return marker_path(LIVE_MARKER)


def live_sync_running(now=None):
    """True when a background live sync was started in the last MARKER_SECONDS."""
    return marker_fresh(LIVE_MARKER, now)


def _live_path(name):
    return os.path.join(cache_dir(), "live", name + ".json")


def read_live(name):
    return _read_json(_live_path(name))


def _record(path, fetch, stub, name, now, write=None):
    """Run fetch() and cache its result at path; on failure keep the old data marked stale
    (stale_since keeps the first failure), or write stub() if nothing was ever cached."""
    write = write or _write_json
    try:
        data = fetch()
        if data.get("schema") != agent.SCHEMA:
            raise AgentError("schema %r != %d, update projdeck" % (data.get("schema"), agent.SCHEMA))
        data.update(host=name, synced_at=now, stale_since=None, last_error=None)
        write(path, data)
        return {"host": name, "ok": True, "error": None}
    except Exception as e:
        err = str(e) or type(e).__name__  # ValueError() has no text: never report a blank
        try:
            old = _read_json(path)
            if old:
                old["stale_since"] = old.get("stale_since") or old.get("synced_at") or now
                old["last_error"] = err
                write(path, old)
            else:  # never synced: stub, so one failed attempt does not block every launch
                write(path, dict(stub, host=name, synced_at=0, stale_since=now,
                                       last_error=err))
        except OSError:
            pass  # marking stale is best effort; the sync result is still reported
        return {"host": name, "ok": False, "error": err}


def load_lives(cfg):
    lives = [read_live(h["name"]) for h in cfg["hosts"]]
    return [c for c in lives if c]


def zellij_in_scope(host):
    """Only a local host that runs zellij has its zellij sessions collected."""
    return bool(host.get("local") and host.get("mux") == "zellij")


def live_request(cfg, host, paths):
    req = {"action": "live", "host": host["name"], "paths": list(paths)}
    if zellij_in_scope(host):
        req["zellij"] = True
    return req


def sync_host(host, cfg, now=None):
    now = int(time.time()) if now is None else int(now)
    stub = {"schema": agent.SCHEMA, "collected_at": 0, "projects": []}
    return _record(_cache_path(host["name"]),
                   lambda: run_agent(host, collector_request(cfg, host), timeout=cfg["ssh_timeout"]),
                   stub, host["name"], now,
                   write=lambda path, data: _write_cache(host["name"], data))


def _cached_paths(host):
    c = read_cache(host["name"])
    return [p["path"] for p in (c or {}).get("projects", []) if p.get("path")]


def sync_live(host, cfg, now=None, memo=None):
    now = int(time.time()) if now is None else int(now)

    def fetch():
        req = live_request(cfg, host, _cached_paths(host))
        if host.get("local"):
            data = agent.act_live(req, memo)
            if not isinstance(data, dict):
                raise AgentError("unexpected live output: %s" % type(data).__name__)
            return data
        return run_agent(host, req, timeout=cfg["ssh_timeout"])

    stub = {"schema": agent.SCHEMA, "collected_at": 0, "sessions": [], "projects": {},
            "zellij_exited": None, "errors": []}
    return _record(_live_path(host["name"]), fetch, stub, host["name"], now)


def _sync_both(host, cfg):
    c = sync_host(host, cfg)
    lv = sync_live(host, cfg)
    return _combine(host, c, lv)


UNKNOWN_ERROR = "未知错误"


def _combine(host, collect, live):
    """One status per host; the error names the layer that failed (a failed layer without
    error text still gets one, so ok=False always comes with an error)."""
    errs = []
    for layer, r in (("collect", collect), ("live", live)):
        if r and (r["error"] or not r["ok"]):
            errs.append("%s: %s" % (layer, r["error"] or UNKNOWN_ERROR))
    return {"host": host["name"],
            "ok": (not collect or collect["ok"]) and (not live or live["ok"]),
            "error": "; ".join(errs) or None}


def sync_all(cfg, names=None, live_only=False, collect_only=False):
    """Sync the given hosts (all by default) in parallel: both layers, or only one."""
    hosts = [h for h in cfg["hosts"] if not names or h["name"] in names]
    if not hosts:
        return []
    if live_only:
        fn = lambda h: _combine(h, None, sync_live(h, cfg))  # noqa: E731
    elif collect_only:
        fn = lambda h: _combine(h, sync_host(h, cfg), None)  # noqa: E731
    else:
        fn = lambda h: _sync_both(h, cfg)  # noqa: E731
    with ThreadPoolExecutor(max_workers=len(hosts)) as ex:
        return list(ex.map(fn, hosts))


def load_caches(cfg):
    caches = [read_cache(h["name"]) for h in cfg["hosts"]]
    return [c for c in caches if c]


def oldest_sync(cfg):
    stamps = []
    for h in cfg["hosts"]:
        c = read_cache(h["name"])
        if not c:
            return None
        stamps.append(c.get("synced_at") or 0)
    return min(stamps) if stamps else None
