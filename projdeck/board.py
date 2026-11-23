"""Pure view-model functions shared by the terminal, the web page and the status line."""
import os
import shlex
import time
from bisect import bisect_right

from . import config
from .config import DEFAULTS as CONFIG_DEFAULTS
from .model import git_failed, human_age, last_activity, tier
from .termtext import has_control, one_line

CELL_MIN_SECONDS = 30
# Tie-break order: earlier wins when coverage is equal.
_PRIORITY = ("wait", "wait_now", "sub", "busy")


def cells(segments, start, n, step):
    """Colour of each of n cells of `step` seconds from `start`: a kind or None."""
    cover = [dict.fromkeys(_PRIORITY, 0) for _ in range(n)]
    end = start + n * step
    for seg in segments:
        kind, s, e = seg[0], seg[1], seg[2]
        if kind not in _PRIORITY:
            continue
        s, e = max(s, start), min(e, end)
        if e <= s:
            continue
        for i in range(int((s - start) // step), min(n, int((e - 1 - start) // step) + 1)):
            lo = start + i * step
            ov = min(e, lo + step) - max(s, lo)
            if ov > 0:
                cover[i][kind] += ov
    out = []
    for c in cover:
        best = None
        for kind in _PRIORITY:
            if c[kind] > CELL_MIN_SECONDS and (best is None or c[kind] > c[best]):
                best = kind
        out.append(best)
    return out


def _midnight(ts, days_back=0):
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday - days_back, 0, 0, 0, 0, 0, -1)))


def today_start(now):
    """Local 00:00 of the day containing `now`."""
    return _midnight(now)


def day_bins(ts_list, now, days=14):
    """Counts per local calendar day, oldest first; the last bin is today."""
    starts = [_midnight(now, days - 1 - i) for i in range(days)]
    bins = [0] * days
    for ts in ts_list:
        if ts > now:
            continue
        i = bisect_right(starts, ts) - 1
        if i >= 0:
            bins[i] += 1
    return bins


# ---- build: session grouping, summary, status-line document ----

# one verdict for "is this host's live data current": every exit (cards, counts, tiles,
# status line) reads host_freshness; age counts from our own clock (synced_at)
FRESH_SECONDS = {"local": 60, "remote": 300}
STALE_SECONDS = {"local": 120, "remote": 600}
LIVE_LABEL_SECONDS = 30
UNSAFE_NAME = "名字含控制字符，先用 `zellij list-sessions` 核对再手动删除"
# "--": a name starting with "-" is not taken as an option (zellij 0.44.3 checked)
DELETE_SESSION = "zellij delete-session -- "
_TODAY_KIND = {"busy": "busy", "sub": "sub", "wait": "wait", "wait_now": "wait"}


def clip_total(segments, lo, hi):
    """Seconds per summary kind (busy / sub / wait) of the segments inside [lo, hi]."""
    out = {"busy": 0, "sub": 0, "wait": 0}
    for seg in segments or ():
        kind = _TODAY_KIND.get(seg[0])
        if kind:
            out[kind] += max(0, min(seg[2], hi) - max(seg[1], lo))
    return out


def _by_host(items):
    return {d.get("host"): d for d in items if d}


def _host_names(caches, lives, cfg):
    names = [h["name"] for h in cfg.get("hosts") or ()]
    for d in list(caches) + list(lives):
        if d and d.get("host") and d["host"] not in names:
            names.append(d["host"])
    return names


def _ended_item(s, host, now_day, commits, now):
    """commits None (git unreadable) leaves commits_today None: unknown, not zero."""
    segs = s.get("segments") or []
    ends = [seg[2] for seg in segs] + [(s.get("last_human") or {}).get("ts") or 0]
    end = max(ends) or s.get("started_at") or 0
    start = s.get("started_at") or (min(seg[1] for seg in segs) if segs else None)
    item = dict(s, host=host, end=end)
    item["duration"] = max(0, end - start) if start else None
    item["wait_total"] = clip_total(segs, now_day, now)["wait"]
    item["commits_today"] = (None if commits is None
                             else sum(1 for t in commits if now_day <= t <= now))
    return item


def _last_commit(path, live_projects, collect_projects):
    """(last_commit {"ts", "subject"} or None, git_failed) of a project. Live data speaks for
    it when it has an entry, otherwise the collect cache. None without failure: no commits
    yet, not a git repo, or not collected."""
    info = live_projects.get(path)
    if isinstance(info, dict):
        if info.get("error") or info.get("commits") is None:
            return None, True
        lc = info.get("last_commit")
        if isinstance(lc, dict) and lc.get("ts"):
            return {"ts": lc["ts"], "subject": lc.get("subject") or ""}, False
        return None, False
    p = collect_projects.get(path)
    if not p:
        return None, False
    if git_failed(p):
        return None, True
    g = p.get("git") or {}
    if g.get("last_commit_ts"):
        return {"ts": g["last_commit_ts"], "subject": g.get("last_commit_subject") or ""}, False
    return None, False


def _attach_for(attach, host, local):
    """design §3.1.8: a remote host's attach command runs through `ssh -t <alias>`."""
    if attach and not local and host.get("ssh"):
        return "ssh -t %s %s" % (shlex.quote(host["ssh"]), shlex.quote(attach))
    return attach


def host_freshness(h, now):
    """{"level": fresh|aging|stale|unknown, "age": seconds or None} for a hosts[] entry."""
    synced = h.get("live_synced_at")
    if h.get("live_unknown") or not synced:
        return {"level": "unknown", "age": None}
    kind = "local" if h.get("local") else "remote"
    age = max(0, int(now - synced))  # a clock running ahead never gives a negative age
    if age <= FRESH_SECONDS[kind]:
        level = "fresh"
    elif age <= STALE_SECONDS[kind]:
        level = "aging"
    else:
        level = "stale"
    return {"level": level, "age": age}


def _host_entry(name, c, lv, cfg_host, local, now):
    errors = [str(e) for e in lv.get("errors") or ()]
    h = {
        "name": name,
        "local": name in local,
        "live_at": lv.get("collected_at") or None,
        "live_synced_at": lv.get("synced_at") or lv.get("collected_at") or None,
        "collect_at": c.get("collected_at") or None,
        "stale_since": lv.get("stale_since") or c.get("stale_since") or None,
        "error": lv.get("last_error") or c.get("last_error") or None,
        "source": lv.get("source"),
        "errors": errors,
        # no successful live collection yet, or no source of live sessions: the state of
        # this host's sessions is unknown (a project's git error does not make it so)
        "live_unknown": not lv.get("collected_at") or lv.get("source") == "none",
        # zellij looked at: in scope, and the live data says what it found (None: not listed)
        "zellij_checked": config.zellij_in_scope(cfg_host) and lv.get("zellij_exited") is not None,
        # D7: show this host's busy/idle sessions as one summary line (config key "brief")
        "brief": cfg_host.get("brief") is True,
    }
    h["freshness"] = host_freshness(h, now)
    return h


def build(caches, lives, cfg, now):
    day = today_start(now)
    cache_by, live_by = _by_host(caches), _by_host(lives)
    cfg_hosts = {h["name"]: h for h in cfg.get("hosts") or ()}
    local = {n for n, h in cfg_hosts.items() if h.get("local")}
    names = _host_names(caches, lives, cfg)

    hosts = []
    for name in names:
        c, lv = cache_by.get(name) or {}, live_by.get(name) or {}
        hosts.append(_host_entry(name, c, lv, cfg_hosts.get(name) or {}, local, now))
    host_by = {h["name"]: h for h in hosts}

    waiting, busy, idle, stale, stale_host, ended, unknown = [], [], [], [], [], [], []
    today = {"busy": 0, "sub": 0, "wait": 0}
    commits_by_project, failed_git = {}, set()
    for name in names:
        c, lv = cache_by.get(name) or {}, live_by.get(name) or {}
        collect_projects = {p.get("path"): p for p in c.get("projects") or ()}
        live_projects = lv.get("projects") or {}
        for p in c.get("projects") or ():
            ts = (p.get("git") or {}).get("commit_ts_14d") or []
            commits_by_project.setdefault((name, p.get("path")), set()).update(ts)
        for path, info in live_projects.items():
            commits_by_project.setdefault((name, path), set()).update(
                (info or {}).get("commits") or [])
        for path in set(collect_projects) | set(live_projects):
            if _last_commit(path, live_projects, collect_projects)[1]:
                failed_git.add((name, path))
        fresh = host_by[name]["freshness"]
        host_stale, age = fresh["level"] == "stale", fresh["age"]
        is_unknown = host_by[name]["live_unknown"]
        for s in lv.get("sessions") or ():
            segs = s.get("segments") or []
            for k, v in clip_total(segs, day, now).items():
                today[k] += v
            key = (name, s.get("project"))
            commits = None if key in failed_git else commits_by_project.get(key, ())
            if is_unknown:  # e.g. source "none": every session reads as ended, which is a guess
                e = _ended_item(s, name, day, commits, now)
                e["state"] = "unknown"
                unknown.append(e)
                continue
            if s.get("state") == "ended" or s.get("live") is False:
                e = _ended_item(s, name, day, commits, now)
                if e["end"] >= day:
                    ended.append(e)
                continue
            item = dict(s, host=name,
                        attach=_attach_for(s.get("attach"), cfg_hosts.get(name) or {},
                                           name in local))
            item["last_commit"], item["git_failed"] = _last_commit(
                s.get("project"), live_projects, collect_projects)
            if s.get("state") == "stale":
                stale.append(item)
            elif host_stale:
                item["age"] = age
                stale_host.append(item)
            elif s.get("state") == "waiting":
                waiting.append(item)
            elif s.get("state") in ("sub", "busy"):
                busy.append(item)
            else:
                idle.append(item)

    waiting.sort(key=lambda s: s.get("idle_since") or 0)
    busy.sort(key=lambda s: -(s.get("started_at") or 0))
    busy.sort(key=lambda s: 0 if s.get("state") == "sub" else 1)
    idle.sort(key=lambda s: -(s.get("idle_since") or 0))
    ended.sort(key=lambda e: -e["end"])
    unknown.sort(key=lambda e: -e["end"])

    open_todos = todo_projects = 0
    for c in caches:
        for p in (c or {}).get("projects") or ():
            n = (p.get("backlog") or {}).get("open") or 0
            open_todos += n
            todo_projects += 1 if n else 0

    summary = {"waiting": len(waiting), "busy": len(busy),
               "sub": sum(1 for s in busy if s.get("state") == "sub"), "idle": len(idle),
               "unknown": len(unknown),
               "open_todos": open_todos, "todo_projects": todo_projects, "today": today}
    out = {"hosts": hosts, "summary": summary, "waiting": waiting, "busy": busy, "idle": idle,
           "ended_today": ended, "unknown": unknown, "stale": stale,
           "stale_host_sessions": stale_host}
    out["built_at"] = now
    out["brief"] = brief_fold(out)
    out["lanes"] = build_lanes(out, commits_by_project, failed_git, now)
    exited = []
    for name in names:
        if not host_by[name]["zellij_checked"]:
            continue  # not looked at: no count, no command
        for sname in live_by[name]["zellij_exited"]:
            # a name that cleaning would change: a copied command would not name the session
            cmd = None if has_control(sname) else DELETE_SESSION + shlex.quote(sname)
            exited.append({"host": name, "name": sname, "cmd": cmd})
    summary["zellij_exited"] = exited
    summary["zellij_exited_n"] = len(exited)
    backlogs, no_backlog = build_backlogs(caches, out, now)
    summary["no_backlog"] = no_backlog
    out["backlogs"] = backlogs
    out["top_todos"] = build_top_todos(caches, backlogs)
    out["tiles"] = build_tiles(caches, out, names, cfg, now)
    return out


# ---- D7 brief hosts: the one fold verdict, its counts, header note and summary label ----

def session_error(s):
    """The session's state could not be read (agent's state_error); a missing field is False."""
    return s.get("state_error") is True


_SESSION_LISTS = ("waiting", "busy", "idle", "ended_today", "unknown", "stale",
                  "stale_host_sessions")


def brief_fold(board):
    """Keys of the sessions shown folded into their brief host's summary line, and per brief
    host the counts behind it. A session folds when it is busy/idle on a brief host, its state
    was read (not session_error) and it has an id: waiting, data gone stale, unknown and ended
    sessions are not in busy/idle, so they always stay listed one by one. folded() reads the
    key only, so a key that any session failing those terms also carries (say an errored twin
    with the same id) folds none of them: such sessions count as unkeyed. Keys and counts
    only, no session copies. Per host: busy_all = folded_busy + errored_busy + unkeyed_busy,
    the same for idle."""
    brief_hosts = {h["name"] for h in board["hosts"] if h.get("brief")}
    blocked = set()  # (host, id) keys carried by a session that may not fold
    for kind in _SESSION_LISTS:
        for s in board.get(kind) or ():
            if not s.get("id"):
                continue
            if (kind not in ("busy", "idle") or s.get("host") not in brief_hosts
                    or session_error(s)):
                blocked.add((s.get("host"), s["id"]))
    hosts, keys = [], []
    for h in board["hosts"]:
        if not h.get("brief"):
            continue
        e = {"host": h["name"], "folded_busy": 0, "folded_idle": 0, "errored_busy": 0,
             "errored_idle": 0, "unkeyed_busy": 0, "unkeyed_idle": 0, "busy_all": 0,
             "idle_all": 0, "warned": 0, "git_failed_projects": 0}
        git_projects = set()
        for kind in ("busy", "idle"):
            for s in board[kind]:
                if s.get("host") != h["name"]:
                    continue
                e[kind + "_all"] += 1
                if session_error(s):
                    e["errored_" + kind] += 1
                elif not s.get("id") or (h["name"], s["id"]) in blocked:
                    e["unkeyed_" + kind] += 1  # no key of its own: list it rather than hide it
                else:
                    e["folded_" + kind] += 1
                    if [h["name"], s["id"]] not in keys:
                        keys.append([h["name"], s["id"]])
                    e["warned"] += 1 if s.get("errors") else 0
                    if s.get("git_failed"):
                        git_projects.add((h["name"], s.get("project")))
        e["git_failed_projects"] = len(git_projects)
        hosts.append(e)
    return {"hosts": hosts, "keys": keys}


def folded(board, s):
    """The one verdict for "this session is shown inside its host's summary line": cards,
    terminal rows, the preview and the swim lanes all ask here. Reads board["brief"]["keys"]
    only; False for a board without "brief" and for a session without an id."""
    brief = board.get("brief")
    if not brief or not s.get("id"):
        return False
    return [s.get("host"), s.get("id")] in brief["keys"]


def brief_note(board, kind):
    """Header note after the busy / idle count, e.g. "（<host> 5 · <host> 2）": how many of
    that count each brief host holds (folded or not). "" when no brief host holds any."""
    parts = ["%s %d" % (e["host"], e[kind + "_all"])
             for e in (board.get("brief") or {}).get("hosts") or () if e[kind + "_all"]]
    return "（%s）" % " · ".join(parts) if parts else ""


def _counted(pairs, sep):
    return sep.join("%s %d" % (word, n) for word, n in pairs if n)


def brief_shown(board, host):
    """Whether the brief host gets its summary (the terminal's summary line, the web's fold
    block): only when a busy/idle session of it is folded, by folded() alone. A stale host's
    sessions are not in busy/idle, so it gets none. Web and terminal both ask here."""
    return any(s.get("host") == host and folded(board, s)
               for kind in ("busy", "idle") for s in board.get(kind) or ())


def brief_label(entry):
    """Summary line text of a brief_fold host entry; web and terminal show the same words.
    Items that are 0 are left out."""
    shown = _counted((("进行中", entry["folded_busy"]), ("空闲", entry["folded_idle"])), " · ")
    out = entry["host"] + (" · " + shown if shown else "")
    apart = _counted((("进行中", entry["errored_busy"] + entry["unkeyed_busy"]),
                      ("空闲", entry["errored_idle"] + entry["unkeyed_idle"])), " · ")
    if apart:
        why = _counted((("出错", entry["errored_busy"] + entry["errored_idle"]),
                        ("无会话 id", entry["unkeyed_busy"] + entry["unkeyed_idle"])), "，")
        out += "（另有单独列出的 %s：%s）" % (apart, why)
    if entry["warned"]:
        out += " · 告警 %d" % entry["warned"]
    if entry["git_failed_projects"]:
        out += " · git 读取失败 %d 个项目" % entry["git_failed_projects"]
    return out


def brief_age(host_entry, now):
    """{"at", "age"} when the host's live data is not fresh (aging, or stale), else None.
    Judged by host_freshness only; at = now - age on our own clock, not the remote live_at."""
    f = host_freshness(host_entry, now)
    if f["level"] in ("aging", "stale"):
        return {"at": now - f["age"], "age": f["age"]}
    return None


def brief_host_age(board, host, now):
    """brief_age of the brief host named host, its entry looked up in board["hosts"]; web,
    terminal line and preview all ask here. A host the board does not list raises
    LookupError: a made-up entry has no live time and would read as fresh."""
    for h in board.get("hosts") or ():
        if h.get("name") == host:
            return brief_age(h, now)
    raise LookupError("brief host %r is not in board hosts" % host)


TOP_TODO_MAX = 5
TILE_DAYS = 14  # heat cells per tile (design: always 14); which projects get one is model.tier
TOP_MARK = "最优先"


def is_top(item):
    """D5: an item is 最优先 when its title or its text says so."""
    return TOP_MARK in (item.get("title") or "") or TOP_MARK in (item.get("text") or "")


def _open_items(backlog):
    return [i for i in (backlog or {}).get("items") or () if not i.get("done")]


def _flagged_backlog_projects(caches):
    """(host, project dict) of projects that have a backlog, flagged first, then newest backlog."""
    rows = [(c.get("host"), p) for c in caches if c for p in c.get("projects") or ()
            if p.get("backlog")]
    rows.sort(key=lambda r: -((r[1]["backlog"].get("mtime")) or 0))
    rows.sort(key=lambda r: 0 if r[1].get("flag") else 1)
    return rows


def build_backlogs(caches, board, now):
    """Per-project backlog entries, plus today's projects that have no backlog file."""
    day = today_start(now)
    out = []
    for host, p in _flagged_backlog_projects(caches):
        b = p["backlog"]
        out.append({"host": host, "project": p.get("path"), "open": b.get("open") or 0,
                    "done": b.get("done") or 0, "updated": b.get("mtime"),
                    "items": [dict(i, top=is_top(i)) for i in b.get("items") or ()],
                    "raw": b.get("raw")})
    today = {(s.get("host"), s.get("project")) for key in ("waiting", "busy", "idle", "ended_today")
             for s in board[key]}
    no_backlog = []
    for c in caches:
        for p in (c or {}).get("projects") or ():
            seen = (c.get("host"), p.get("path")) in today or any(
                (s.get("last_ts") or 0) >= day for s in p.get("sessions") or ())
            entry = {"host": c.get("host"), "project": p.get("path")}
            if seen and not p.get("backlog") and entry not in no_backlog:
                no_backlog.append(entry)
    return out, no_backlog


def build_top_todos(caches, backlogs):
    """D5: items marked 最优先 first, then the first open item of every flagged project."""
    flagged = {(c.get("host"), p.get("path")) for c in caches if c
               for p in c.get("projects") or () if p.get("flag")}
    marked, firsts = [], []
    for b in backlogs:
        is_flag = (b["host"], b["project"]) in flagged
        todo = _open_items(b)
        for i in todo:
            if i["top"]:
                marked.append((b, i, is_flag))
        if is_flag and todo:
            firsts.append((b, todo[0], is_flag))
    out, seen = [], set()
    for b, i, is_flag in marked + firsts:
        key = (b["host"], b["project"], i.get("title"))
        if key not in seen:
            seen.add(key)
            out.append({"host": b["host"], "project": b["project"], "title": i.get("title"),
                        "flag": is_flag})
    return out[:TOP_TODO_MAX]


def _headline(s):
    if s.get("state") == "waiting" and s.get("question"):
        return s["question"]
    return s.get("title") or s.get("last_reply") or s.get("name") or ""


def _host_trusted(h, now):
    """Live data of this host can say "no session runs": checked and not stale."""
    return host_freshness(h, now)["level"] in ("fresh", "aging")


def build_tiles(caches, board, names, cfg, now):
    """One tile per recently active, backlog-carrying or flagged project. Without trusted live
    data for the host (unknown or stale) a tile with no fresh session says "unknown"."""
    live = {}
    for key in ("waiting", "busy", "idle"):  # the first session of a project speaks for it
        for s in board[key]:
            live.setdefault((s.get("host"), s.get("project")), []).append(s)
    trusted = {h["name"]: _host_trusted(h, now) for h in board["hosts"]}
    tiers_days = cfg.get("tiers_days") or CONFIG_DEFAULTS["tiers_days"]
    order = {n: i for i, n in enumerate(names)}
    tiles = []
    for c in sorted((c for c in caches if c), key=lambda c: order.get(c.get("host"), 0)):
        host = c.get("host")
        for p in c.get("projects") or ():
            p_tier = tier(p, now, tiers_days)  # the same active tier the terminal groups by
            if p_tier == "deleted" or not (p.get("flag") or p.get("backlog") or p_tier == "active"):
                continue
            sessions = live.get((host, p.get("path")), [])
            states = {s.get("state") for s in sessions}
            state = "wait" if "waiting" in states else \
                "busy" if states & {"busy", "sub"} else \
                "none" if trusted.get(host) else "unknown"
            git = p.get("git") or {}
            todos = _open_items(p.get("backlog"))
            failed = git_failed(p)
            if sessions:
                text = _headline(sessions[0])
            elif todos:
                text = todos[0].get("title") or ""
            elif failed:
                text = "git 读取失败"
            elif git:
                ts = git.get("last_commit_ts") or 0
                text = ("最近提交 %s 前：%s" % (human_age(now - ts), git.get("last_commit_subject") or "")
                        if ts else "还没有提交")
            else:
                text = "不是 git 仓库"
            tasks = next((s["tasks"].get("items") or [] for s in sessions
                          if isinstance(s.get("tasks"), dict)), [])
            tiles.append({"host": host, "project": p.get("path"), "state": state,
                          "todo": (p.get("backlog") or {}).get("open") or 0,
                          "commits_14d": (None if failed
                                          else day_bins(git.get("commit_ts_14d") or [], now, TILE_DAYS)),
                          "tasks": [i.get("status") for i in tasks], "text": text,
                          "_act": last_activity(p)})
    tiles.sort(key=lambda t: -t["_act"])
    tiles.sort(key=lambda t: {"wait": 0, "busy": 1, "unknown": 2, "none": 2}[t["state"]])
    for t in tiles:
        t.pop("_act")
    return tiles


LANE_MIN_SECONDS = 3 * 3600
LANE_MAX_ROWS = 12


def _lane_window(sessions, day, now):
    """Window (start, now): starts at the earlier of now - 3h and the hour of today's first
    activity (activity before 00:00 is clipped to 00:00). So it is at least 3h wide, and
    before 03:00 the 3h minimum can reach back past midnight into yesterday."""
    firsts = [max(seg[1], day) for s in sessions for seg in s.get("segments") or ()
              if seg[2] > day and seg[1] < now]
    start = now - LANE_MIN_SECONDS
    if firsts:
        start = min(start, day + (min(firsts) - day) // 3600 * 3600)
    return start, now


def build_lanes(board, commits_by_project, failed_git, now):
    """Swim-lane rows for the overview timeline, ordered like the board columns. failed_git:
    (host, project) keys whose git could not be read (no commit marks is not "no commits")."""
    day = today_start(now)
    ordered = [(s, s.get("state")) for s in board["waiting"] + board["busy"] + board["idle"]]
    ordered += [(s, "ended") for s in board["ended_today"]]
    ordered += [(s, "unknown") for s in board.get("unknown") or () if s["end"] >= day]
    start, end = _lane_window([s for s, _ in ordered], day, now)
    # a folded session's lane goes to its host's brief block; each part marks a project's
    # commits once, so a project split across both shows them on both
    rows, brief_rows, commit_shown = [], {}, {}
    for s, state in ordered:
        part = s.get("host") if folded(board, s) else None
        out = rows if part is None else brief_rows.setdefault(part, [])
        shown = commit_shown.setdefault(part, set())
        key = (s.get("host"), s.get("project"))
        commits = []
        if key not in shown:
            shown.add(key)
            commits = sorted(t for t in commits_by_project.get(key, ()) if start <= t <= end)
        tasks = s.get("tasks")
        owns = isinstance(tasks, dict) and not s.get("tasks_owner")
        merges = [[m[0], m[1]] for m in (tasks.get("merges") or ())
                  if start <= m[1] <= end] if owns else []
        segs = [[k, max(a, start), min(b, end)] for k, a, b in s.get("segments") or ()
                if min(b, end) > max(a, start)]
        total = clip_total(segs, start, end)
        out.append({"host": s.get("host"), "project": s.get("project"), "session_id": s.get("id"),
                    "state": state, "segments": segs, "commits": commits, "merges": merges,
                    "git_failed": key in failed_git,
                    "busy_s": total["busy"], "wait_s": total["wait"]})
    brief = [{"host": e["host"], "rows": brief_rows[e["host"]][:LANE_MAX_ROWS],
              "n": len(brief_rows[e["host"]]),
              "more": max(0, len(brief_rows[e["host"]]) - LANE_MAX_ROWS)}
             for e in (board.get("brief") or {}).get("hosts") or () if brief_rows.get(e["host"])]
    return {"start": start, "end": end, "rows": rows[:LANE_MAX_ROWS],
            "more": max(0, len(rows) - LANE_MAX_ROWS), "brief": brief}


def statusline_doc(board, now):
    """What the status-line segment reads: sessions waiting on you, same order as the board.
    updated is when local live data last succeeded (never after now), so a local collection
    that keeps failing ages the file out instead of re-stamping old data; None without it."""
    local_at = [h.get("live_at") for h in board.get("hosts") or () if h.get("local")]
    updated = int(min(min(local_at), now)) if local_at and all(local_at) else None
    return {"updated": updated,
            "waiting": [{"host": s.get("host"),
                         "project": one_line(os.path.basename((s.get("project") or "").rstrip("/"))),
                         "name": one_line(s.get("name") or ""), "session_id": s.get("id"),
                         "since": s.get("idle_since")}
                        for s in board["waiting"]]}
