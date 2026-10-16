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


def read_cache(name):
    try:
        with open(_cache_path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_cache(name, data):
    os.makedirs(cache_dir(), exist_ok=True)
    path = _cache_path(name)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def sync_host(host, cfg, now=None):
    now = int(time.time()) if now is None else int(now)
    try:
        data = run_agent(host, collector_request(cfg, host), timeout=cfg["ssh_timeout"])
        if data.get("schema") != agent.SCHEMA:
            raise AgentError("schema %r != %d, update projdeck" % (data.get("schema"), agent.SCHEMA))
        data.update(host=host["name"], synced_at=now, stale_since=None, last_error=None)
        _write_cache(host["name"], data)
        return {"host": host["name"], "ok": True, "error": None}
    except (AgentError, OSError) as e:
        try:
            old = read_cache(host["name"])
            if old:
                old["stale_since"] = old.get("stale_since") or old.get("synced_at") or now
                old["last_error"] = str(e)
                _write_cache(host["name"], old)
            else:  # never synced: stub, so one failed attempt does not block every launch
                _write_cache(host["name"], {
                    "schema": agent.SCHEMA, "host": host["name"], "collected_at": 0,
                    "projects": [], "synced_at": 0, "stale_since": now, "last_error": str(e)})
        except OSError:
            pass  # marking stale is best effort; the sync result is still reported
        return {"host": host["name"], "ok": False, "error": str(e)}


def sync_all(cfg, names=None):
    hosts = [h for h in cfg["hosts"] if not names or h["name"] in names]
    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=len(hosts)) as ex:
        return list(ex.map(lambda h: sync_host(h, cfg), hosts))


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
