"""Load ~/.config/proj/config.json and fill in defaults."""
import copy
import json
import os
import socket

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
            cfg.update(json.load(f))
    if not cfg["hosts"]:
        cfg["hosts"] = [{"name": socket.gethostname().split(".")[0], "local": True,
                         "roots": ["~"]}]
    for h in cfg["hosts"]:
        if not h.get("name") or not (h.get("local") or h.get("ssh")):
            raise ValueError('host %r needs a "name" and either "ssh" or "local": true' % h)
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
