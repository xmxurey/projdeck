"""Load ~/.config/proj/config.json and fill in defaults."""
import copy
import json
import math
import os
import re
import socket
import sys

from projdeck.vendor.webkit.webkit import check_bind

HOST_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")

DEFAULTS = {
    "hosts": [],
    "tiers_days": {"active": 14, "stale": 90},
    "scan_depth": 2,
    "include_plain": True,
    "auto_sync_minutes": 30,
    "ssh_timeout": 20,
    "suspicious_patterns": None,  # None: use the agent's DEFAULT_PATTERNS
    "trash_dir": "~/.proj-trash",
    "warnings": [],  # filled by load_config, shown on top of the list
    "serve": {"bind": None, "port": 8787, "push_statusline": True},  # `proj serve`
    "dashboard_url": None,  # where the terminal's ^V opens the dashboard
}


MUX_VALUES = ("zellij", "tmux", False)


def resolve_mux(h, platform=None):
    """The host's terminal multiplexer: "mux" if given, else the older "tmux" key.
    zellij works on local Linux hosts only (it is found through /proc)."""
    platform = platform or sys.platform
    mux = h["mux"] if "mux" in h else ("tmux" if h.get("tmux", True) else False)
    if mux not in MUX_VALUES:
        raise ValueError('host %r: "mux" must be "zellij", "tmux" or false' % h.get("name"))
    if mux == "zellij" and not h.get("local"):
        return False, 'host %s: "mux": "zellij" works on the local host only; using a plain shell' % h["name"]
    if mux == "zellij" and not platform.startswith("linux"):
        return False, 'host %s: "mux": "zellij" needs Linux (/proc); using a plain shell' % h["name"]
    return mux, None


def valid_port(port):
    """A listening port: an integer in 1-65535 (bools are not ports)."""
    return not isinstance(port, bool) and isinstance(port, int) and 1 <= port <= 65535


def check_serve(s, path):
    """Validate the merged "serve" object; errors name the file, the key and what is allowed."""
    if not valid_port(s["port"]):
        raise ValueError('config %s: "serve.port" must be an integer in 1-65535, got %r'
                         % (path, s["port"]))
    bind = s["bind"]
    if bind is not None:
        try:
            if not isinstance(bind, str):  # ip_address() would take 2130706433 as 127.0.0.1
                raise ValueError(bind)
            check_bind(bind)
        except ValueError:
            raise ValueError('config %s: "serve.bind" must be null or an IP address that is '
                             'loopback or in 100.64.0.0/10, got %r' % (path, bind))
    if not isinstance(s["push_statusline"], bool):
        raise ValueError('config %s: "serve.push_statusline" must be true or false, got %r'
                         % (path, s["push_statusline"]))


def check_positive(cfg, key, path):
    """A duration setting: a positive, finite number (bools are not numbers here)."""
    v = cfg[key]
    if (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            or v <= 0):
        raise ValueError('config %s: "%s" must be a positive number, got %r' % (path, key, v))


def config_path():
    return os.path.expanduser(os.environ.get("PROJ_CONFIG", "~/.config/proj/config.json"))


def load_config(path=None):
    path = path or config_path()
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("config %s must be a JSON object" % path)
        tiers = user.pop("tiers_days", None)
        serve = user.pop("serve", None)
        cfg.update(user)
        if tiers is not None:
            if not isinstance(tiers, dict):
                raise ValueError('"tiers_days" must be an object')
            cfg["tiers_days"] = dict(DEFAULTS["tiers_days"], **tiers)
        if serve is not None:
            if not isinstance(serve, dict):
                raise ValueError('config %s: "serve" must be an object' % path)
            cfg["serve"] = dict(DEFAULTS["serve"], **serve)
    check_serve(cfg["serve"], path)
    for key in ("ssh_timeout", "auto_sync_minutes"):
        check_positive(cfg, key, path)
    if cfg["dashboard_url"] is not None and not isinstance(cfg["dashboard_url"], str):
        raise ValueError('config %s: "dashboard_url" must be a string or null' % path)
    if not cfg["hosts"]:
        cfg["hosts"] = [{"name": socket.gethostname().split(".")[0], "local": True,
                         "roots": ["~"]}]
    names = set()
    for h in cfg["hosts"]:
        if not h.get("name") or not (h.get("local") or h.get("ssh")):
            raise ValueError('host %r needs a "name" and either "ssh" or "local": true' % h)
        name = h["name"]
        if (not isinstance(name, str) or not HOST_NAME_RE.fullmatch(name)
                or name.startswith(".")):
            raise ValueError("invalid host name %r: use letters, digits, '.', '_' or '-', "
                             "and do not start with '.'" % (name,))
        if name in names:
            raise ValueError("duplicate host name %r" % name)
        names.add(name)
        h["mux"], warning = resolve_mux(h)
        if warning:
            cfg["warnings"].append(warning)
        if h.get("ssh") is not None and (not isinstance(h["ssh"], str)
                                         or h["ssh"].startswith("-")):
            raise ValueError("invalid ssh alias %r for host %r" % (h["ssh"], name))
    return cfg


def host_by_name(cfg, name):
    for h in cfg["hosts"]:
        if h["name"] == name:
            return h
    raise ValueError("unknown host %r (see %s)" % (name, config_path()))


def collector_request(cfg, host):
    return {"action": "collect", "host": host["name"], "roots": host.get("roots", ["~"]),
            "scan_depth": cfg["scan_depth"], "include_plain": cfg["include_plain"],
            "patterns": cfg["suspicious_patterns"]}
