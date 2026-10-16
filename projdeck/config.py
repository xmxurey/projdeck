"""Load ~/.config/proj/config.json and fill in defaults."""
import copy
import json
import os
import re
import socket

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
}


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
        cfg.update(user)
        if tiers is not None:
            if not isinstance(tiers, dict):
                raise ValueError('"tiers_days" must be an object')
            cfg["tiers_days"] = dict(DEFAULTS["tiers_days"], **tiers)
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
