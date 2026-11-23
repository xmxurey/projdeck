"""fzf-based pickers and the preview pane."""
import os
import re
import shlex
import subprocess
import sys
import time

from projdeck import board, paths
from projdeck.board import cells, clip_total, host_freshness
from projdeck.model import ROW_DEFAULTS, find_row, TIER_ICON, git_errors, git_failed, human_age, latest_session, n_suspicious, span, sync_age
from projdeck.termtext import has_surrogates, one_line as _one_line, safe as _safe

SEP = "\t"
# path field of a brief host's summary line: real paths are absolute or hex:-encoded
BRIEF_FIELD = "brief:"
STATE_ERROR_MARK = "⚠ 状态读取失败"  # brief host session listed because its state was unreadable
PROJECT_KEYS = ["ctrl-r", "ctrl-y", "ctrl-f", "ctrl-x", "ctrl-u", "ctrl-l", "ctrl-v"]
PROJECT_HEADER = ("Enter: resume · ^R: resume latest · ^Y: pick session · ^F: search prompts · "
                  "^X: clean · ^U: sync · ^L: backlog · ^V: dashboard")

# muted truecolor tones, one per registry tag in file order (cycled beyond six)
TAG_PALETTE = [(141, 164, 191),   # steel blue
               (147, 174, 143),   # sage
               (196, 169, 126),   # sand
               (181, 149, 174),   # mauve
               (127, 174, 170),   # sea glass
               (190, 145, 132)]   # clay
RESET = "\x1b[0m"
DIM = "\x1b[2m"
YELLOW = "\x1b[33m"
TL_CELLS, TL_STEP = 24, 450          # 24 cells x 7.5 min = the last 3 hours
HOST_ERROR_CHARS = 60                # the first live error of a host, cut to this in the header
EMPTY_CELL = (42, 47, 55)
CELL_RGB = {"busy": (57, 135, 229), "sub": (25, 158, 112), "wait": (217, 89, 38),
            "wait_now": (217, 89, 38)}
WAIT_RGB, DOING_RGB, GREY_RGB, DONE_RGB, STUCK_RGB = (
    (217, 89, 38), (57, 135, 229), (128, 128, 128), (12, 163, 12), (230, 103, 103))
CELL_GLYPH = {"wait_now": "▒"}       # same colour as "wait", told apart by texture
STUCK_GLYPH = "!"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s):
    return ANSI_RE.sub("", s)


def tag_order(rows):
    order = []
    for r in rows:
        for t in list(r.get("tags") or []) + [r.get("tag")]:
            if t and t not in order:
                order.append(t)
    return order


def tag_color(tag, order):
    if not tag or tag not in order:
        return ""
    return "\x1b[38;2;%d;%d;%dm" % TAG_PALETTE[order.index(tag) % len(TAG_PALETTE)]


def _paint(text, tag, order):
    c = tag_color(tag, order or [])
    return c + text + RESET if c else text


def tag_legend(order):
    return "   ".join(_paint("● " + _one_line(t), t, order) for t in order)


class Cancelled(Exception):
    pass


_UNESCAPABLE_RE = re.compile("[\ud800-\udc7f\udd00-\udfff]")
HEX_MARK = "hex:"


def to_field(text):
    """A value for a hidden fzf field. fzf rewrites invalid UTF-8 in {n} placeholders, so a
    value with surrogate characters (non-UTF-8 bytes, surrogateescape) or one that starts
    with the marker travels as `hex:<hex of its bytes>`, pure ASCII; any other value as is."""
    text = _UNESCAPABLE_RE.sub("\ufffd", text)
    if has_surrogates(text) or text.startswith(HEX_MARK):
        return HEX_MARK + text.encode("utf-8", "surrogateescape").hex()
    return text


def from_field(text):
    """Inverse of to_field."""
    if text.startswith(HEX_MARK):
        try:
            return bytes.fromhex(text[len(HEX_MARK):]).decode("utf-8", "surrogateescape")
        except ValueError:
            pass
    return text


def run_fzf(lines, args, expect=()):
    argv = ["fzf"] + list(args)
    if expect:
        argv.append("--expect=" + ",".join(expect))
    try:
        r = subprocess.run(argv, input="\n".join(lines), stdout=subprocess.PIPE,
                           encoding="utf-8", errors="surrogateescape")
    except FileNotFoundError:
        raise SystemExit("fzf is required: brew install fzf")
    if r.returncode in (1, 130):  # 1: no match, 130: aborted (Esc / Ctrl-C)
        raise Cancelled()
    if r.returncode != 0:
        raise SystemExit("fzf failed (exit %d); fzf >= 0.36 is required" % r.returncode)
    out = r.stdout.split("\n")
    key = out.pop(0) if expect else ""
    return key, [line for line in out if line]


def preview_cmd():
    return "%s -m projdeck _preview {1} {2}" % shlex.quote(sys.executable)


def spreview_cmd():
    return "%s -m projdeck _spreview {1} {2} {3} {4}" % shlex.quote(sys.executable)


def file_preview_cmd(host_name, root):
    return "%s -m projdeck _fpreview %s %s {1}" % (shlex.quote(sys.executable),
                                                   shlex.quote(host_name), shlex.quote(root))


def _ago(seconds):
    """'5m 前', or '—' when the age is not known (never shown as <1m)."""
    return "—" if seconds is None else "%s 前" % human_age(seconds)


def _activity_age(r, now):
    """Age column of a row; a live-only row with no time at all shows '—'."""
    return "—" if r.get("activity") is None else human_age(now - r["activity"])


def project_name(r):
    """Path relative to the host's home (daily/digest), else the last component."""
    rel = paths.rel_to_home(r["path"], r.get("home"))
    if rel is not None:
        return rel
    return os.path.basename(r["path"].rstrip("/")) or r["path"]


def _shown_name(r):
    """project_name made safe for display (it derives from the untrusted path)."""
    return _one_line(project_name(r))


def live_flag(r):
    """▶name for one zellij session (▶name·0 when no claude runs in it), else ▶count."""
    z, t = r.get("live_zellij") or [], r.get("live_tmux") or []
    if len(z) == 1 and not t:
        return "▶%s%s" % (_one_line(z[0]["name"]), "" if z[0]["claude"] else "·0")
    return "▶%d" % (len(z) + len(t)) if z or t else ""


def pick_live(live, name):
    lines = ["%s\t%d claude" % (_one_line(s["name"]), s["claude"]) for s in live]
    chosen = pick_line(lines, "%s has %d live zellij sessions: pick one" % (_one_line(name), len(live)))
    return live[lines.index(chosen)]


def _fg(rgb, text):
    return "\x1b[38;2;%d;%d;%dm%s%s" % (rgb + (text, RESET))


def spark(cells_):
    """One character per cell (▆, or ▒ for wait_now), coloured by kind (None = empty cell)."""
    return "".join(_fg(CELL_RGB.get(k, EMPTY_CELL), CELL_GLYPH.get(k, "▆")) for k in cells_)


def task_bar(tasks):
    """Segmented task bar '■■▣□ 2/4 T07'; over 16 tasks just '12/21 ▣T13' ('!T13' when stuck); stuck cells are a red '!'.
    `tasks` is the session's task dict (items, current) or a bare item list."""
    items = tasks.get("items") if isinstance(tasks, dict) else tasks
    if not items:
        return ""
    current = _one_line(tasks.get("current")) if isinstance(tasks, dict) else None
    done = sum(1 for i in items if i.get("status") == "done")
    if not current:
        current = next((_one_line(i.get("id")) for i in items if i.get("status") == "doing"), None)
    if len(items) > 16:
        mark = ""
        if current:
            stuck = any(i.get("status") == "stuck" for i in items if _one_line(i.get("id")) == current)
            mark = " " + (_fg(STUCK_RGB, STUCK_GLYPH) if stuck else _fg(DOING_RGB, "▣")) + current
        return "%d/%d%s" % (done, len(items), mark)
    glyph = {"done": _fg(DONE_RGB, "■"), "doing": _fg(DOING_RGB, "▣"),
             "stuck": _fg(STUCK_RGB, STUCK_GLYPH)}
    bar = "".join(glyph.get(i.get("status"), _fg(GREY_RGB, "□")) for i in items)
    return "%s %d/%d%s" % (bar, done, len(items), " " + current if current else "")


def _live_tail(live, now):
    state = live.get("state")
    if state == "waiting":
        return _one_line(live.get("question")) or _one_line(live.get("title"))
    if state in ("sub", "busy"):
        owns = isinstance(live.get("tasks"), dict) and not live.get("tasks_owner")
        return (task_bar(live["tasks"]) if owns else "") or _one_line(
            live.get("title") or live.get("name"))
    if state == "idle":
        since = live.get("idle_since")
        return "空闲 %s" % (human_age(now - since) if since else "—")
    return ""


def _marker(live, r):
    state = (live or {}).get("state")
    if "age" in (live or {}):
        state = None            # remote data too old to trust: keep the tier icon
    if state == "waiting":
        return _fg(WAIT_RGB, "◆")
    if state in ("sub", "busy"):
        return _fg(DOING_RGB, "◉")
    if state == "idle":
        return DIM + "◌" + RESET
    return TIER_ICON[r["tier"]]


def project_flags(r, git=None):
    """The marks after a project's name (▶ ± ↑ ⊘ ⚑ 💬 git? non-git). git: None judges git
    failure from the row (the project line), True / False is the caller's verdict (the
    summary preview gives a session's git_failed, the same source as the summary line)."""
    g = r.get("git") or {}
    flags = []
    if live_flag(r):
        flags.append(live_flag(r))
    if g.get("dirty"):
        flags.append("±%d" % g["dirty"])
    if g.get("ahead"):
        flags.append("↑%d" % g["ahead"])
    if r.get("git") and g.get("no_upstream"):
        flags.append("⊘")
    if n_suspicious(r):
        flags.append("⚑%d" % n_suspicious(r))
    if r["sessions"]:
        flags.append("💬%d" % len(r["sessions"]))
    known = r["exists"] and not r.get("uncollected")  # uncollected: git unknown
    if (known and git_failed(r)) if git is None else git:
        flags.append("git?")
    elif known and not r.get("git") and not git_failed(r):  # no git and no git error
        flags.append("non-git")
    return flags


def project_line(r, now, order=None, show_host=False, live=None):
    """live: the board session that speaks for this project ({} when it has none), or None
    for the plain line without timeline."""
    flags = project_flags(r)
    if show_host:
        flags.insert(0, "%s@%s%s" % (DIM, _one_line(r["host"]), RESET))
    if live is None:
        name = _paint("%-28s" % _shown_name(r)[:28], r.get("tag"), order)
        display = "%s %s %5s  %s" % (TIER_ICON[r["tier"]], name, _activity_age(r, now),
                                     " ".join(flags))
        return SEP.join([r["host"], to_field(r["path"]), display])
    name = _paint("%-18s" % _shown_name(r)[:18], r.get("tag"), order)
    line = cells(live.get("segments") or [], now - TL_CELLS * TL_STEP, TL_CELLS, TL_STEP)
    tail = []
    if "age" in live:
        tail.append(YELLOW + "⚠ live 数据 %s" % _ago(live["age"]) + RESET)
    else:
        tail.append(_live_tail(live, now))
    open_todos = (r.get("backlog") or {}).get("open") or 0
    if open_todos and ("age" in live or not live.get("state")):
        tail.append("☐%d" % open_todos)
    display = "%s %s %5s %s %s  %s" % (_marker(live, r), name, _activity_age(r, now),
                                       spark(line), " ".join(flags), " ".join(t for t in tail if t))
    return SEP.join([r["host"], to_field(r["path"]), display.rstrip()])


def _header(host, label):
    label = _one_line(label)
    return SEP.join([host, "", "── %s %s" % (label, "─" * max(3, 40 - len(label)))])


_HOME_RE = re.compile(r"^(/(?:home|Users)/[^/]+)/")


def _session_time(s):
    """Latest moment the session itself shows: segment end, idle_since or started_at; None
    when it shows none (not now: that would read as "just active")."""
    stamps = [seg[2] for seg in s.get("segments") or ()] + [s.get("idle_since") or 0,
                                                             s.get("started_at") or 0]
    return max(stamps) or None


def _minimal_row(host, path, session, now):
    r = dict(ROW_DEFAULTS)
    m = _HOME_RE.match(path)
    r.update(host=host, path=path, uncollected=True, local=False, ssh=host, tags=[], tier="active", activity=_session_time(session),
             home=m.group(1) if m else None)
    return r


URGENCY = {"waiting": 0, "sub": 1, "busy": 1, "idle": 2}


def _speakers(bd):
    """(host, project) -> the trusted session that speaks for it: one not folded into its
    host's summary line first, then the most urgent; plus the stale-host ones. A folded
    session speaks only when every trusted session of the project is folded."""
    fresh, stale = {}, {}

    def rank(s):
        return board.folded(bd, s), URGENCY.get(s.get("state"), 2)
    for key in ("waiting", "busy", "idle"):
        for s in bd.get(key) or ():
            k = (s.get("host"), s.get("project"))
            if k not in fresh or rank(s) < rank(fresh[k]):
                fresh[k] = s
    for s in bd.get("stale_host_sessions") or ():
        k = (s.get("host"), s.get("project"))
        if k not in fresh and k not in stale:
            stale[k] = dict(s, age=s.get("age"))  # None stays unknown
    return fresh, stale


def preview_target(board, rows, host, path, now):
    """(row, live) the preview of host:path shows: the cached row (or, for a project only
    the live data knows, a minimal one) and the session standing for it. (None, None) when
    nothing knows the project."""
    fresh, stale = _speakers(board)
    live = fresh.get((host, path)) or stale.get((host, path))
    return _project_row(rows, host, path, live, now), live


def _project_row(rows, host, path, session, now):
    """The cached row of host:path, or, for a project only the live data knows (it has a
    session), a minimal one; None when neither knows it."""
    row = find_row(rows, host, path)
    if row is None and session:
        row = _minimal_row(host, path, session, now)
    return row


def _summary_line(bd, entry, now):
    """fzf line of a brief host's summary: [host, BRIEF_FIELD, blue ◉ + brief_label]; all
    yellow with the data time when the host's data is not fresh."""
    label = _one_line(board.brief_label(entry))
    age = board.brief_host_age(bd, entry["host"], now)
    if age:
        display = YELLOW + "◉ %s · 数据 %s（%s）" % (label, _clock(age["at"]), _ago(age["age"])) + RESET
    else:
        display = _fg(DOING_RGB, "◉") + " " + label
    return SEP.join([entry["host"], BRIEF_FIELD, display])


def _progress_groups(bd, now, live_of, by_key, taken, line):
    """◆ 等你 and ◉ 进行中 groups. A project whose speaking session is folded is taken (not
    listed, not back in its host group); each brief host with folded sessions gets one
    summary line at the end of ◉ 进行中. In both groups, the row of a brief host's project
    holding a busy / idle session whose state could not be read ends with STATE_ERROR_MARK,
    whichever session speaks for the project: the summary line says that session is listed
    apart, and this row is where it is."""
    entries = (bd.get("brief") or {}).get("hosts") or ()
    brief_hosts = {e["host"] for e in entries}
    errored = {(s.get("host"), s.get("project"))
               for kind in ("busy", "idle") for s in bd.get(kind) or ()
               if s.get("host") in brief_hosts and board.session_error(s)}
    lines = []
    for label, states in (("◆ 等你", ("waiting",)), ("◉ 进行中", ("sub", "busy", "idle"))):
        progress = label == "◉ 进行中"
        members = []
        for state in states:
            for k, s in live_of.items():
                if s.get("state") == state and "age" not in s and k in by_key and k not in taken:
                    taken.add(k)
                    if board.folded(bd, s):
                        continue
                    members.append(line(by_key[k], True) + (
                        "  " + YELLOW + STATE_ERROR_MARK + RESET if k in errored else ""))
        if progress:
            members += [_summary_line(bd, e, now) for e in entries
                        if board.brief_shown(bd, e["host"])]
        if members:
            lines.append(_header("", label))
            lines.extend(members)
    return lines


def project_lines(rows, now, board=None):
    """With a board: ◆ 等你 and ◉ 进行中 groups on top, then 🚩 优先, then each host's other
    projects. Without: flagged projects of every host in a 🚩 group, then the host groups."""
    order = tag_order(rows)
    if board is None:
        live_of, plain = {}, True
    else:
        fresh, stale = _speakers(board)
        live_of, plain = dict(stale), False
        live_of.update(fresh)
        rows = list(rows)
        known = {(r["host"], r["path"]) for r in rows}
        for k, sn in live_of.items():
            if k not in known and k[0] and k[1]:
                rows.append(_minimal_row(k[0], k[1], sn, now))
                known.add(k)
        first = {}
        for r in rows:
            first.setdefault(r["host"], len(first))
        rows.sort(key=lambda r: first[r["host"]])   # stable: keeps a host's rows together
    lines, taken = [], set()

    def line(r, show_host=False):
        k = (r["host"], r["path"])
        return project_line(r, now, order, show_host,
                            None if plain else live_of.get(k, {}))

    if not plain:
        # in-progress group: sub, then busy, idle last
        lines += _progress_groups(board, now, live_of, {(r["host"], r["path"]): r for r in rows},
                                  taken, line)
    flagged = sorted((r for r in rows if r.get("flag") and (r["host"], r["path"]) not in taken),
                     key=lambda r: -r["activity"])
    if flagged:
        lines.append(_header("", "🚩 优先"))
        lines.extend(line(r, True) for r in flagged)
    host = None
    for r in rows:
        if r.get("flag") or (r["host"], r["path"]) in taken:
            continue
        if r["host"] != host:
            host = r["host"]
            lines.append(_header(host, host))
        lines.append(line(r))
    return lines


def _clock(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _counts(bd):
    """Header counts; busy and idle note each brief host's share, e.g. 进行中 3（<host> 2）."""
    s = bd.get("summary") or {}
    return "%s  %s  %s  ☐ 待办 %s" % (
        _fg(WAIT_RGB, "◆") + " 等你 %d" % (s.get("waiting") or 0),
        _fg(DOING_RGB, "◉") + " 进行中 %d%s" % (s.get("busy") or 0,
                                              _one_line(board.brief_note(bd, "busy"))),
        DIM + "◌" + RESET + " 空闲 %d%s" % (s.get("idle") or 0, _one_line(board.brief_note(bd, "idle"))),
        s.get("open_todos") if s.get("open_todos") is not None else "—")


def header_lines(board, now):
    """The two overview header lines: host data times with today's counts, and today's totals."""
    segs, hinted = [], False
    for h in board.get("hosts") or ():
        at = h.get("live_at")
        text = "%s 数据 %s" % (_one_line(h["name"]), _clock(at) if at else "—")
        if h.get("source") == "none":
            text += " 活会话来源不可用"
        errs = h.get("errors") or []
        if errs:
            text += " ⚠ %s%s" % (_one_line(errs[0])[:HOST_ERROR_CHARS],
                                 " 等 %d 条" % len(errs) if len(errs) > 1 else "")
        err = h.get("error")
        if err:
            text += " ⚠ 同步失败：%s" % _one_line(err)[:HOST_ERROR_CHARS]
        if not at or host_freshness(h, now)["level"] != "fresh" or h.get("source") == "none" \
                or errs or err:
            text = YELLOW + text + ("" if hinted else " · ^U 刷新") + RESET
            hinted = True
        segs.append(text)
    s = board.get("summary") or {}
    first = "proj  %s      %s" % (" · ".join(segs), _counts(board))
    if s.get("zellij_exited_n"):
        first += " · 已退出 zellij %d（proj report 看清理命令）" % s["zellij_exited_n"]
    today = s.get("today") or {}
    second = "今天 %s Claude %s  %s 子代理 %s  %s 闲置等输入 %s      时间线 = 最近 3 小时，每格 7.5 分钟" % (
        _fg(CELL_RGB["busy"], "■"), span(today.get("busy")),
        _fg(CELL_RGB["sub"], "■"), span(today.get("sub")),
        _fg(CELL_RGB["wait"], "■"), span(today.get("wait")))
    return [first, second]


def session_line(r, s, now):
    display = "%-8s %5s  %-20s %s%s" % (_one_line(r["host"])[:8], human_age(now - s["last_ts"]),
                                        _shown_name(r)[:20], _one_line(s["title"]),
                                        "  [headless]" if s.get("headless") else "")
    return SEP.join([r["host"], to_field(r["path"]), to_field(s["id"]), display])


def prompt_line(r, s, text, now):
    where = "%s:%s" % (_one_line(r["host"]), _shown_name(r))
    text = _one_line(text)
    display = "%5s  %-28s │ %s" % (human_age(now - s["last_ts"]), where[:28], text)
    return SEP.join([r["host"], to_field(r["path"]), to_field(s["id"]), text, display])


def file_line(f):
    return SEP.join([to_field(f["path"]), "%s %s  (%dK)" % ("⚑" if f["suspicious"] else " ", _one_line(f["path"]),
                                                   f["size"] // 1024)])


def _left_off(s, now):
    if not (s.get("recent") or s.get("last_reply")):
        return []
    out = ["", "left off · %s · %s ago" % (_safe(s["title"]), human_age(now - s["last_ts"]))]
    out.extend("  › " + _safe(p) for p in s.get("recent") or [])
    if s.get("last_reply"):
        out.append("  ⏺ " + _safe(s["last_reply"]))
    return out


def session_preview_text(r, s, now, prompt):
    return "\n".join(["› " + _safe(prompt), "", "%s:%s" % (_safe(r["host"]), _safe(r["path"])),
                      "session: %s · %s ago" % (_safe(s["title"]), human_age(now - s["last_ts"]))]
                     + _left_off(s, now))


PV_CELLS, PV_STEP = 48, 450          # preview timeline: the last 6 hours
SESSION_TEXT_MAX = 300
TASK_GLYPH = {"done": "✓", "doing": "◉", "stuck": "!"}


def _cap(text, n=SESSION_TEXT_MAX):
    text = _safe(text).strip()
    return text if len(text) <= n else text[:n - 1] + "…"


def _zellij_note(r, live):
    z = r.get("live_zellij") or []
    if z:
        return "zellij " + ", ".join("%s（%d claude）" % (_safe(x["name"]), x["claude"]) for x in z)
    m = re.match(r"zellij attach (\S+)", live.get("attach") or "")
    return "zellij " + _safe(m.group(1)) if m else ""


def _status_line(r, live, now):
    state = live.get("state")
    if "age" in live:
        return YELLOW + "⚠ live 数据 %s，会话状态可能已过期" % _ago(live["age"]) + RESET
    since = live.get("idle_since")
    if state == "waiting":
        return _fg(WAIT_RGB, "◆") + " 等你 %s" % (span(now - since) if since else "—")
    if state in ("sub", "busy"):
        started = live.get("started_at")
        text = ["运行中 " + (span(now - started) if started else "—")]
        note = _zellij_note(r, live)
        if note:
            text.append(note)
        return _fg(DOING_RGB, "◉") + " " + " · ".join(text)
    if state == "idle":
        return DIM + "◌" + RESET + " 空闲 %s" % (span(now - since) if since else "—")
    return ""


def _live_section(r, live, now):
    """Preview lines for the session behind the project: state, 6h timeline, tasks, last words."""
    out = []
    status = _status_line(r, live, now)
    if status:
        out.append(status)
    if r.get("uncollected"):
        out.append(YELLOW + "该项目还没采集，按 ^U 同步（下面只有会话状态与时间线）" + RESET)
    start = now - PV_CELLS * PV_STEP
    segs = live.get("segments") or []
    out.append(_timeline_title(start, now))
    out.append(spark(cells(segs, start, PV_CELLS, PV_STEP)))
    t = clip_total(segs, start, now)
    out.append("Claude %s · 子代理 %s · 闲置等输入 %s" % (span(t["busy"]), span(t["sub"]), span(t["wait"])))
    tk = live.get("tasks")
    if isinstance(tk, dict) and tk.get("items"):
        items = tk["items"]
        done = sum(1 for i in items if i.get("status") == "done")
        name = _one_line(tk.get("label") or tk.get("path"))
        out.append("── 计划 %s%d/%d ──" % (name + " · " if name else "· ", done, len(items)))
        for i in items:
            out.append("%s %s %s" % (TASK_GLYPH.get(i.get("status"), "○"), _one_line(i.get("id")),
                                      _one_line(i.get("title"))))
    elif live.get("tasks_owner"):
        out.append("（计划由同一检出的另一个会话负责）")
    out.extend(["── Claude 最后说 ──", _cap(live.get("last_reply")) or "—"])
    return out


def preview_text(r, now, live=None):
    out = ["%s:%s" % (_safe(r["host"]), _safe(r["path"])),
           "%s %s · last activity %s" % (TIER_ICON[r["tier"]], r["tier"],
                                         "—" if r.get("activity") is None
                                         else human_age(now - r["activity"]) + " ago")]
    if r.get("stale_since"):
        out.append("⚠ host data is from %s ago (last sync failed)" % human_age(now - r["stale_since"]))
    g = r.get("git")
    if r.get("uncollected"):
        pass                     # nothing collected: git / directory facts would mislead
    elif g:
        out.append("git: %s  ±%d ↑%d  untracked %d%s  %s" % (
            _one_line(g["branch"]), g["dirty"], g["ahead"], g["untracked"],
            "  (no upstream)" if g["no_upstream"] else "",
            "no commits yet" if not g["last_commit_ts"]
            else "last commit %s ago" % human_age(now - g["last_commit_ts"])))
    elif r["exists"] and git_failed(r):
        out.append("git 读取失败: %s" % "; ".join(_one_line(e) for e in git_errors(r)))
    elif r["exists"]:
        out.append("not a git repo")
    else:
        out.append("directory no longer exists")
    running = bool(live) and live.get("state") in ("sub", "busy") and "age" not in live
    if r.get("live_zellij") and not running:  # the running status line already names them
        out.append("▶ zellij: %s" % ", ".join("%s (%d claude)" % (_safe(s["name"]), s["claude"])
                                              for s in r["live_zellij"]))
    if r.get("live_tmux"):
        out.append("▶ live tmux: %s" % ", ".join(_safe(x) for x in r["live_tmux"]))
    s = latest_session(r)
    if s:
        out.extend(_left_off(s, now))
    if r["suspicious"]:
        out.append("")
        out.append("suspicious files (%d):" % n_suspicious(r))
        out.extend("  " + _safe(s) for s in r["suspicious"][:10])
    if r["progress"]:
        out.extend(["", "progress:"] + ["  " + line for line in _safe(r["progress"]).splitlines()])
    if r["sessions"]:
        out.extend(["", "sessions (%d):" % len(r["sessions"])])
        for s in r["sessions"][:8]:
            out.append("  %5s  %s%s" % (human_age(now - s["last_ts"]), _safe(s["title"]),
                                        "  [headless]" if s.get("headless") else ""))
    errors = list(r.get("errors") or []) + list((live or {}).get("errors") or [])
    if errors:
        out.extend(["", "errors:"] + ["  " + _safe(e) for e in errors])
    if live:
        out[1:1] = _live_section(r, live, now)
    return "\n".join(out)


def _timeline_title(start, now):
    return "── 最近 6 小时 ── %s ─── %s ──" % (_clock(start), _clock(now))


def _folded_session_lines(rows, s, now):
    """One folded session in the summary preview: project · session · state and the project
    marks (git? from the session's git_failed, as the summary line counts it), its 6h
    timeline, its task bar and each of its warnings."""
    host, path = s.get("host"), s.get("project") or ""
    r = _project_row(rows, host, path, s, now)
    if s.get("state") in ("sub", "busy"):
        marker, word, since = _fg(DOING_RGB, "◉"), "运行中", s.get("started_at")
    else:
        marker, word, since = DIM + "◌" + RESET, "空闲", s.get("idle_since")
    head = "%s %s · %s · %s %s" % (marker, _shown_name(r), _one_line(s.get("name") or s.get("title")),
                                   word, span(now - since) if since else "—")
    flags = project_flags(r, git=bool(s.get("git_failed")))
    out = [head + ("  " + " ".join(flags) if flags else "")]
    out.append(spark(cells(s.get("segments") or [], now - PV_CELLS * PV_STEP, PV_CELLS, PV_STEP)))
    if isinstance(s.get("tasks"), dict) and not s.get("tasks_owner") and task_bar(s["tasks"]):
        out.append(task_bar(s["tasks"]))
    out.extend("  ⚠ " + _safe(e) for e in s.get("errors") or ())
    return out


def brief_preview_text(bd, rows, host, now):
    """Preview of a brief host's summary line: the summary text, a warning when the host's
    data is not fresh, then every session folded into it (busy first, idle after, in board
    order), each with its 6h timeline."""
    entry = next((e for e in (bd.get("brief") or {}).get("hosts") or () if e["host"] == host),
                 None)
    if entry is None:
        return "%s：没有精简展示的会话" % _one_line(host)
    out = [_one_line(board.brief_label(entry))]
    age = board.brief_host_age(bd, host, now)
    if age:
        out.append(YELLOW + "⚠ live 数据 %s（%s），会话状态可能已过期" % (
            _clock(age["at"]), _ago(age["age"])) + RESET)
    out.append(_timeline_title(now - PV_CELLS * PV_STEP, now))
    for s in list(bd.get("busy") or ()) + list(bd.get("idle") or ()):
        if s.get("host") == host and board.folded(bd, s):
            out.extend(_folded_session_lines(rows, s, now))
    return "\n".join(out)


def bpreview_cmd():
    return "%s -m projdeck _bpreview {1} {2} {3}" % shlex.quote(sys.executable)


def host_preview_text(host, caches, rows, now):
    mine = [r for r in rows if r["host"] == host]
    out = ["%s: %d project%s" % (_one_line(host), len(mine), "" if len(mine) == 1 else "s")]
    for c in caches:
        if c["host"] == host:
            out.append(sync_age(c, now))
            if c.get("stale_since"):
                out.append("⚠ last sync failed: %s" % _safe(c.get("last_error")))
    for t in ("active", "stale", "abandoned", "deleted"):
        n = sum(1 for r in mine if r["tier"] == t)
        if n:
            out.append("  %s %s: %d" % (TIER_ICON[t], t, n))
    return "\n".join(out)


def pick_project(rows, now, notice=None, select=None, board=None):
    """select: (host, path) to put the cursor on, e.g. the project opened last."""
    legend = tag_legend(tag_order(rows))
    header = PROJECT_HEADER + ("\n" + legend if legend else "") + ("\n" + _safe(notice) if notice else "")
    if board is not None:
        header = "\n".join(header_lines(board, now)) + "\n" + header
    lines = project_lines(rows, now, board)
    extra = []
    if select:
        want = SEP.join([select[0], to_field(select[1])]) + SEP
        pos = next((i for i, ln in enumerate(lines, 1) if ln.startswith(want)), None)
        if pos:
            extra.append("--bind=load:pos(%d)" % pos)
    # --no-sort keeps the host grouping while filtering
    key, sel = run_fzf(lines, extra + [
                               "--ansi", "--delimiter=\t", "--with-nth=3..", "--no-sort",
                               "--layout=reverse",
                               "--header=" + header] + (["--header-first"] if board is not None else []) + [
                               "--preview=" + preview_cmd(),
                               "--preview-window=right,50%,wrap"], expect=PROJECT_KEYS)
    if not sel:
        raise Cancelled()
    host, path, _ = sel[0].split(SEP, 2)
    return key, host, from_field(path)


def _backlog_rows(rows):
    """Projects with something to show, flagged first, then newest backlog first."""
    have = [r for r in rows if (r.get("backlog") or {}).get("items") or (r.get("backlog") or {}).get("raw")]
    return sorted(have, key=lambda r: (not r.get("flag"), -((r["backlog"].get("mtime")) or 0)))


def backlog_lines(rows):
    shown = _backlog_rows(rows)
    multi = len({r["host"] for r in shown}) > 1
    lines = []
    for r in shown:
        b = r["backlog"]
        name = _shown_name(r) + ("@" + _one_line(r["host"]) if multi else "")
        lines.append(SEP.join([r["host"], to_field(r["path"]), "", "── %s ☐%d · 已完成 %d（未显示）──" % (
            name, b.get("open") or 0, b.get("done") or 0)]))
        if not b.get("items"):
            lines.append(SEP.join([r["host"], to_field(r["path"]), "", "  （无法解析，预览看全文）"]))
        for idx, it in enumerate(b["items"]):
            if not it.get("done"):
                lines.append(SEP.join([r["host"], to_field(r["path"]), str(idx), "  ☐ " + _one_line(it.get("title"))]))
    return lines


def backlog_preview_text(row, idx):
    """Body of one backlog item (idx = index into the full item list), or the whole file when
    it could not be parsed; with no index, a summary of the project's backlog."""
    b = row.get("backlog") or {}
    if not b:
        return "%s:%s\n(没有 docs/backlog.md)" % (_safe(row["host"]), _safe(row["path"]))
    if not b.get("items"):
        return _safe(b.get("raw")) or "(空)"
    try:
        it = b["items"][int(idx)]
    except (TypeError, ValueError, IndexError):
        return "\n".join(["%s:%s" % (_safe(row["host"]), _safe(row["path"])),
                          "☐ %d · 已完成 %d" % (b.get("open") or 0, b.get("done") or 0)]
                         + ["  ☐ " + _one_line(i.get("title")) for i in b["items"] if not i.get("done")])
    title, section = _one_line(it.get("title")), _one_line(it.get("section"))
    return "\n".join([("%s · %s" % (title, section)) if section else title, "",
                      _safe(it.get("text"))])


def pick_backlog(rows, now):
    """^L view: open backlog items grouped by project; returns the chosen project (host, path)."""
    _, sel = run_fzf(backlog_lines(rows), [
        "--ansi", "--delimiter=\t", "--with-nth=4..", "--no-sort", "--layout=reverse",
        "--header=Enter: back to the project (read-only view)",
        "--preview=" + bpreview_cmd(), "--preview-window=right,60%,wrap"])
    if not sel:
        raise Cancelled()
    host, path, _ = sel[0].split(SEP, 2)
    return host, from_field(path)


def pick_session(pairs, now):
    lines = [session_line(r, s, now) for r, s in pairs]
    _, sel = run_fzf(lines, ["--delimiter=\t", "--with-nth=4..", "--tiebreak=index",
                             "--header=Enter: resume in a new tab",
                             "--preview=" + preview_cmd(), "--preview-window=right,40%,wrap"])
    if not sel:
        raise Cancelled()
    host, path, sid, _ = sel[0].split(SEP, 3)
    return host, from_field(path), from_field(sid)


def pick_prompt(items, now, query=""):
    lines = [prompt_line(r, s, t, now) for r, s, t in items]
    args = ["--delimiter=\t", "--with-nth=5..", "--exact", "--tiebreak=index",
            "--header=Enter: resume in a new tab · 'word: fuzzy",
            "--preview=" + spreview_cmd(), "--preview-window=right,45%,wrap"]
    if query:
        args.append("--query=" + query)
    _, sel = run_fzf(lines, args)
    if not sel:
        raise Cancelled()
    host, path, sid, _ = sel[0].split(SEP, 3)
    return host, from_field(path), from_field(sid)


def pick_files(files, preview=None):
    files = sorted(files, key=lambda f: (not f["suspicious"], f["path"]))
    k = sum(1 for f in files if f["suspicious"])
    args = ["--multi", "--layout=reverse", "--delimiter=\t", "--with-nth=2..",
            "--header=Tab: toggle · Enter: confirm (⚑ = matches junk rules, preselected)"]
    if preview:
        args += ["--preview=" + preview, "--preview-window=right,50%,wrap"]
    if k:
        # `load` fires once piped input is read; `start` can fire before items exist.
        args.append("--bind=load:" + "+".join(["toggle+down"] * k) + "+first")
    _, sel = run_fzf([file_line(f) for f in files], args)
    return [from_field(line.split(SEP, 1)[0]) for line in sel]


def pick_line(lines, header, shown_fields=None):
    """shown_fields: fzf --with-nth over tab-separated lines, e.g. "3.." to keep raw key
    fields out of sight (they are returned as given, only the shown part is cleaned)."""
    args = ["--header=" + _safe(header), "--layout=reverse"]
    if shown_fields:
        args += ["--delimiter=\t", "--with-nth=" + shown_fields]
    _, sel = run_fzf(lines, args)
    if not sel:
        raise Cancelled()
    return sel[0]


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
