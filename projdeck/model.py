"""Turn per-host caches into one sorted list of project rows."""
TIER_ICON = {"active": "●", "stale": "◐", "abandoned": "○", "deleted": "✗"}
ROW_DEFAULTS = {"exists": True, "git": None, "last_mtime": 0, "suspicious": [], "progress": "",
                "sessions": [], "errors": []}


def last_activity(p):
    stamps = [p.get("last_mtime") or 0] + [s["last_ts"] for s in p.get("sessions", [])]
    if p.get("git"):
        stamps.append(p["git"].get("last_commit_ts") or 0)
    return max(stamps)


def tier(p, now, tiers_days):
    if not p.get("exists", True):
        return "deleted"
    age_days = (now - last_activity(p)) / 86400.0
    if age_days < tiers_days["active"]:
        return "active"
    if age_days < tiers_days["stale"]:
        return "stale"
    return "abandoned"


def rows(caches, cfg, now):
    hosts = {h["name"]: h for h in cfg["hosts"]}
    out = []
    for c in caches:
        h = hosts.get(c["host"], {"ssh": c["host"]})
        for p in c["projects"]:
            r = dict(ROW_DEFAULTS)
            r.update(p)
            r.update(host=c["host"], local=bool(h.get("local")), ssh=h.get("ssh"),
                     activity=last_activity(p), tier=tier(p, now, cfg["tiers_days"]),
                     stale_since=c.get("stale_since"))
            out.append(r)
    # group by host in config order (unknown hosts last), newest first within a host
    order = {h["name"]: i for i, h in enumerate(cfg["hosts"])}
    out.sort(key=lambda r: (order.get(r["host"], len(order)), r["host"], -r["activity"]))
    return out


def n_suspicious(r):
    """Total suspicious files; the list itself is capped by the agent."""
    return r.get("suspicious_count", len(r.get("suspicious") or []))


def find_row(rows_, host, path):
    for r in rows_:
        if r["host"] == host and r["path"] == path:
            return r
    return None


def all_sessions(rows_):
    pairs = [(r, s) for r in rows_ for s in r["sessions"]]
    pairs.sort(key=lambda pair: -pair[1]["last_ts"])
    return pairs


def latest_session(row):
    sessions = row["sessions"]
    for s in sessions:
        if not s.get("headless"):
            return s
    return sessions[0] if sessions else None


def human_age(seconds):
    s = max(0, int(seconds))
    for unit, n in (("y", 365 * 86400), ("mo", 30 * 86400), ("d", 86400), ("h", 3600),
                    ("m", 60)):
        if s >= n:
            return "%d%s" % (s // n, unit)
    return "<1m"
