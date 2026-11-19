"""Overview page: header, swim-lane timeline, the four kanban columns and the backlog area.

Every piece of session or project text is untrusted; it goes through h() before it reaches
HTML. Drawing uses SVG attributes only (no style attributes, no inline script, no external URL).
"""
import os
import re
import time

from projdeck.board import TOP_MARK, UNSAFE_NAME
from projdeck.model import human_age, span  # noqa: F401 (span: part of web's API)
from projdeck.vendor.webkit.webkit import h, layout

SEG_CLASS = {"busy": "busy", "sub": "sub", "wait": "wait", "wait_now": "wait-now"}
SEG_LABEL = {"busy": "Claude 自己在干活", "sub": "子代理在干活",
             "wait": "闲置等输入", "wait_now": "还在闲置等输入"}
SEG_FILL = {"busy": "#2F5D8A", "sub": "#8DB4DC", "wait": "#E8954A", "wait_now": "#F6CFA6"}
TASK_FILL = {"done": "#2F7D5B", "doing": "#2F5D8A", "todo": "#D5D9E0", "stuck": "#C0392B"}
TASK_MARK = {"done": "✓", "doing": "◉", "todo": "○", "stuck": "!"}
FOLD_OVER = 12

TL_W, TL_LABEL_W, TL_RIGHT_W, LANE_H, AXIS_H = 1000, 230, 190, 34, 24
_HOME = re.compile(r"^/(?:home|Users)/[^/]+/(.+)$")


def project_name(path):
    """Path relative to the home directory (dev/projdeck), as the terminal shows it."""
    p = (path or "").rstrip("/")
    home = os.path.expanduser("~").rstrip("/")
    if home and p.startswith(home + "/"):
        return p[len(home) + 1:]
    m = _HOME.match(p)
    if m:
        return m.group(1)
    return p.rsplit("/", 1)[-1] or (path or "")


def _clock(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _now(board, now=None):
    """The moment the page describes: the request time when the caller gives one (serve does),
    else the snapshot's own time, so a page rendered from a board is reproducible."""
    if now is not None:
        return now
    end = board.get("built_at") or (board.get("lanes") or {}).get("end")
    return end if end else time.time()


BOARD_STALE_SECONDS = 60  # the snapshot is rebuilt every 15 s; older than this, refreshing stopped


def _board_stale(board, now):
    built = board.get("built_at")
    if not built or now - built <= BOARD_STALE_SECONDS:
        return ""
    return ('<p class="board-stale">看板停止刷新：数据停在 %s（%s 前），下面的内容可能已过期</p>'
            % (h(_clock(built)), h(span(now - built))))


def _sessions(board):
    return (list(board.get("waiting") or ()) + list(board.get("busy") or ())
            + list(board.get("idle") or ()) + list(board.get("ended_today") or ()))


def _name_of(board, session_id, host=None):
    for s in _sessions(board):
        if s.get("id") == session_id and (host is None or s.get("host") == host):
            return s.get("name") or s.get("title") or session_id
    return session_id


# ---------------------------------------------------------------- timeline

def _lane_label(row, name):
    state = {"waiting": "等你", "busy": "进行中", "sub": "进行中", "idle": "空闲",
             "ended": "已结束", "unknown": "状态未知"}.get(row.get("state"), "")
    return project_name(row.get("project")), "%s · %s" % (name or "", state)


def render_timeline(board):
    lanes = board.get("lanes") or {}
    start, end = lanes.get("start") or 0, lanes.get("end") or 1
    rows = lanes.get("rows") or []
    more = lanes.get("more") or 0
    width = max(1, end - start)
    plot_w = TL_W - TL_LABEL_W - TL_RIGHT_W

    def x(t):
        return TL_LABEL_W + (min(max(t, start), end) - start) / width * plot_w

    height = AXIS_H + LANE_H * max(1, len(rows)) + 4
    out = ['<section id="timeline">', "<h2>今天的会话都在干什么</h2>",
           '<svg class="timeline-svg" viewBox="0 0 %d %d" width="100%%" role="img" '
           'aria-label="泳道时间线">' % (TL_W, height)]
    t = start - start % 3600
    while t <= end:
        if t >= start:
            out.append('<text class="axis" x="%.1f" y="14" text-anchor="middle" font-size="11">%s</text>'
                       % (x(t), h(_clock(t))))
            out.append('<line class="grid" x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#E3E6EB"/>'
                       % (x(t), AXIS_H - 4, x(t), height))
        t += 3600
    for i, row in enumerate(rows):
        y = AXIS_H + i * LANE_H
        name = _name_of(board, row.get("session_id"), row.get("host"))
        proj, sub = _lane_label(row, name)
        out.append('<g class="lane lane-%s">' % h(row.get("state")))
        out.append('<line class="lane-sep" x1="0" y1="%d" x2="%d" y2="%d" stroke="#F1F3F6"/>'
                   % (y, TL_W, y))
        out.append('<text class="lane-project" x="0" y="%d" font-size="13" font-weight="600">%s</text>'
                   % (y + 15, h(proj)))
        out.append('<text class="lane-state" x="0" y="%d" font-size="11">%s</text>'
                   % (y + 28, h(sub)))
        for kind, a, b in row.get("segments") or ():
            cls = SEG_CLASS.get(kind)
            if cls is None:
                continue
            title = "%s %s–%s" % (SEG_LABEL[kind], _clock(a), _clock(b))
            out.append('<rect class="seg seg-%s %s" x="%.1f" y="%d" width="%.1f" height="16" rx="2">'
                       '<title>%s</title></rect>'
                       % (cls, cls, x(a), y + 6, max(1.0, x(b) - x(a)), h(title)))
        for ts in row.get("commits") or ():
            out.append('<polygon class="mark mark-commit" fill="#1A1D21" points="%s">'
                       '<title>提交 %s</title></polygon>' % (_diamond(x(ts), y + 30), h(_clock(ts))))
        for tid, ts in row.get("merges") or ():
            out.append('<polygon class="mark mark-merge" fill="#2F7D5B" points="%s">'
                       '<title>任务合并 %s %s</title></polygon>'
                       % (_diamond(x(ts), y + 30), h(tid), h(_clock(ts))))
            out.append('<text class="mark-label" x="%.1f" y="%d" font-size="10">%s</text>'
                       % (x(ts) + 6, y + 33, h(tid)))
        out.append('<text class="lane-total" x="%d" y="%d" font-size="12">干活 %s · 闲置等输入 %s</text>'
                   % (TL_W - TL_RIGHT_W + 10, y + 20, h(span(row.get("busy_s"))),
                      h(span(row.get("wait_s")))))
        if row.get("git_failed"):  # no commit marks here means "unknown", not "none today"
            out.append('<text class="lane-git-failed" x="%d" y="%d" font-size="10">git 读取失败</text>'
                       % (TL_W - TL_RIGHT_W + 10, y + 32))
        out.append("</g>")
    now_x = x(end)
    out.append('<line class="now-line" x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#C0392B" '
               'stroke-width="1.5"><title>现在</title></line>' % (now_x, AXIS_H - 4, now_x, height))
    out.append("</svg>")
    if more > 0:
        out.append('<p class="timeline-more">另有 %d 个会话</p>' % more)
    out.append('<ul class="legend">')
    for kind in ("busy", "sub", "wait", "wait_now"):
        out.append('<li><svg width="14" height="10" viewBox="0 0 14 10"><rect class="%s" width="14" '
                   'height="10" rx="2"/></svg> %s</li>' % (SEG_CLASS[kind], h(SEG_LABEL[kind])))
    out.append('<li><svg width="10" height="10" viewBox="0 0 10 10"><polygon fill="#1A1D21" '
               'points="5,0 10,5 5,10 0,5"/></svg> 提交</li>')
    out.append('<li><svg width="10" height="10" viewBox="0 0 10 10"><polygon fill="#2F7D5B" '
               'points="5,0 10,5 5,10 0,5"/></svg> 任务合并</li>')
    out.append("</ul></section>")
    return "\n".join(out)


def _diamond(cx, cy, r=4):
    return "%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" % (cx, cy - r, cx + r, cy, cx, cy + r, cx - r, cy)


# ----------------------------------------------------------------- columns

def _head(s):
    return ('<div class="card-head"><span class="card-project">%s</span>'
            '<span class="card-who">%s · %s</span></div>'
            % (h(project_name(s.get("project"))), h(s.get("host")), h(s.get("name") or s.get("title"))))


def _attach(s):
    cmd = s.get("attach")
    if cmd:
        return '<p class="card-attach"><code>%s</code></p>' % h(cmd)
    return '<p class="card-attach muted">（不在 zellij / tmux 里）</p>'


def _errors(s):
    errs = (s.get("errors") or [])[:2]
    return "".join('<p class="card-error muted">⚠ %s</p>' % h(e) for e in errs)


def _subagents(s):
    n = s.get("subagents_running") or 0
    return '<p class="card-sub">子代理 %s 个在跑</p>' % h(n) if n > 0 else ""


def _last_commit(s, now):
    """§3.5 「最近提交」: age and subject; "git 读取失败" when git failed, "—" when there is none."""
    lc = s.get("last_commit")
    if isinstance(lc, dict) and lc.get("ts"):
        text = "最近提交 %s 前：%s" % (human_age(now - lc["ts"]), lc.get("subject") or "")
    else:
        text = "最近提交：%s" % ("git 读取失败" if s.get("git_failed") else "—")
    return '<p class="card-commit muted">%s</p>' % h(text)


def _wait_card(s, now):
    since = s.get("idle_since")
    idle = "已闲置 %s，在等你" % span(now - since) if since else "在等你"
    return ('<article class="card card-wait">%s<p class="card-idle">%s</p>'
            '<p class="card-question">%s</p>%s%s%s%s</article>'
            % (_head(s), h(idle), h(s.get("question") or s.get("last_reply")),
               _subagents(s), _last_commit(s, now), _attach(s), _errors(s)))


def _task_bar(items):
    n = len(items)
    w = 300
    gap = 2
    cell = max(2.0, (w - gap * (n - 1)) / n)
    rects = []
    for i, it in enumerate(items):
        st = it.get("status") if it.get("status") in TASK_FILL else "todo"
        rects.append('<rect class="task-%s%s" fill="%s" x="%.1f" y="0" width="%.1f" height="8" rx="2">'
                     '<title>%s %s</title></rect>'
                     % (st, " done" if st == "done" else "", TASK_FILL[st], i * (cell + gap), cell,
                        h(it.get("id")), h(it.get("title"))))
    return ('<svg class="task-bar" viewBox="0 0 %d 8" width="100%%" role="img" aria-label="任务进度">%s</svg>'
            % (w, "".join(rects)))


def _task_row(it):
    st = it.get("status") if it.get("status") in TASK_MARK else "todo"
    note = ' <span class="task-note muted">%s</span>' % h(it["note"]) if it.get("note") else ""
    return ('<li class="task task-%s"><span class="task-mark">%s</span> <span class="task-id">%s</span> '
            '<span class="task-title">%s</span>%s</li>'
            % (st, TASK_MARK[st], h(it.get("id")), h(it.get("title")), note))


def _tasks(board, s):
    tasks = s.get("tasks")
    if not isinstance(tasks, dict):
        owner = s.get("tasks_owner")
        if owner:
            return ('<p class="card-owner muted">同一检出的计划由 %s 负责</p>'
                    % h(_name_of(board, owner)))
        return ""
    items = [i for i in tasks.get("items") or () if isinstance(i, dict)]
    if not items:
        return ""
    done = [i for i in items if i.get("status") == "done"]
    prog = "%d / %d" % (len(done), len(items))
    if tasks.get("current"):
        prog += " · 当前 %s" % tasks["current"]
    if len(items) > FOLD_OVER and done:
        rest = [i for i in items if i.get("status") != "done"]
        rows = ('<li><details data-key="%s"><summary>已完成 %d 个</summary><ul class="tasks">%s</ul>'
                '</details></li>%s'
                % (h("tasks-%s-%s" % (s.get("host"), s.get("id"))), len(done), "".join(_task_row(i) for i in done), "".join(_task_row(i) for i in rest)))
    else:
        rows = "".join(_task_row(i) for i in items)
    return ('%s<p class="card-progress">%s</p><ul class="tasks">%s</ul>'
            '<p class="card-source muted">%s</p>'
            % (_task_bar(items), h(prog), rows,
               h(tasks.get("label") or tasks.get("path") or "")))


def _last_said(s):
    lh = s.get("last_human") or {}
    out = ""
    if lh.get("text"):
        note = ""
        if s.get("last_input") == "peer":
            note = ' <span class="muted">（最近一条输入来自子代理交回）</span>'
        out += '<p class="card-human">你最后说：%s%s</p>' % (h(lh["text"]), note)
    if s.get("last_reply"):
        out += '<p class="card-reply">Claude 最后说：%s</p>' % h(s["last_reply"])
    return out


def _busy_card(board, s, now):
    if s.get("state") == "sub":
        tag = "子代理 %s 个在跑" % (s.get("subagents_running") or 0)
    elif s.get("started_at"):
        tag = "运行中 %s" % span(now - s["started_at"])
    else:
        tag = "运行中"
    return ('<article class="card card-busy">%s<p class="card-state">%s</p>%s%s%s%s%s</article>'
            % (_head(s), h(tag), _tasks(board, s), _last_said(s), _last_commit(s, now),
               _attach(s), _errors(s)))


def _idle_card(s, now):
    since = s.get("idle_since")
    return ('<article class="card card-idle-session">%s<p class="card-state muted">◌ 空闲 %s</p>%s%s</article>'
            % (_head(s), h(span(now - since) if since else "—"), _last_said(s), _errors(s)))


def _ended_card(s):
    n = s.get("commits_today")
    commits = "git 读取失败" if n is None and "commits_today" in s else "提交 %s 次" % (n or 0)
    dur = "时长 %s · 闲置等输入 %s · %s" % (span(s.get("duration")), span(s.get("wait_total")), commits)
    return ('<article class="card card-ended">%s<p class="card-end">结束于 %s</p>'
            '<p class="card-stats">%s</p>%s</article>'
            % (_head(s), h(_clock(s["end"]) if s.get("end") else "—"), h(dur), _errors(s)))


def _todo_card(t):
    flag = "🚩 " if t.get("flag") else ""
    return ('<article class="card card-todo"><p class="card-project">%s%s</p><p>%s</p></article>'
            % (flag, h(project_name(t.get("project"))), h(t.get("title"))))


def _stale_host(card, s):
    """Grey out a card whose host data is older than 10 minutes and say how old."""
    age = s.get("age")
    note = ('<p class="card-age">%d 分钟前的数据</p>' % int(max(0, age) // 60) if age is not None
            else '<p class="card-age">数据时间 —</p>')
    card = card.replace('<article class="card ', '<article class="card card-stale-host ', 1)
    return card[:-len("</article>")] + note + "</article>"


def _column(cls, title, count, cards, empty, anchor=None):
    head = '<h3>%s <span class="count">%s</span></h3>' % (h(title), h(count)) if count is not None \
        else "<h3>%s</h3>" % h(title)
    body = "".join(cards) or '<p class="muted">%s</p>' % h(empty)
    if anchor:
        head = '<a id="%s"></a>%s' % (anchor, head)
    return '<div class="column %s">%s%s</div>' % (cls, head, body)


def render_columns(board, now=None):
    now = _now(board, now)
    waiting = board.get("waiting") or []
    busy = board.get("busy") or []
    idle = board.get("idle") or []
    ended = board.get("ended_today") or []
    todos = board.get("top_todos") or []
    stale_wait, stale_busy, stale_idle = [], [], []
    for s in board.get("stale_host_sessions") or ():
        st = s.get("state")
        if st == "waiting":
            stale_wait.append(_stale_host(_wait_card(s, now), s))
        elif st in ("busy", "sub"):
            stale_busy.append(_stale_host(_busy_card(board, s, now), s))
        else:
            stale_idle.append(_stale_host(_idle_card(s, now), s))
    cols = [
        _column("col-wait", "等你", len(waiting), [_wait_card(s, now) for s in waiting] + stale_wait,
                "没有会话在等你", "wait"),
        _column("col-busy", "进行中", len(busy),
                [_busy_card(board, s, now) for s in busy] + stale_busy
                + [_idle_card(s, now) for s in idle] + stale_idle,
                "没有进行中的会话", "busy"),
        _column("col-ended", "今天结束", len(ended), [_ended_card(s) for s in ended], "今天还没有结束的会话"),
        _column("col-todo col-top", "最优先待办", None, [_todo_card(t) for t in todos], "没有最优先待办"),
    ]
    return '<section id="columns">%s</section>' % "".join(cols)


# ----------------------------------------------------------- stale sessions

def render_unknown_fold(board):
    """Sessions of hosts whose live state could not be read: listed, never sorted into columns."""
    unknown = board.get("unknown") or []
    if not unknown:
        return ""
    rows = "".join('<li>%s · %s · %s</li>' % (h(project_name(s.get("project"))), h(s.get("host")),
                                              h(s.get("name") or s.get("title")))
                   for s in unknown)
    return ('<details class="unknown-fold stale-fold" data-key="unknown-sessions" open>'
            '<summary>%d 个会话状态未知（活会话来源不可用，看不出是否还在跑）</summary><ul>%s</ul>'
            '</details>' % (len(unknown), rows))


def render_stale_fold(board):
    stale = board.get("stale") or []
    if not stale:
        return ""
    rows = "".join('<li>%s · %s · %s</li>' % (h(project_name(s.get("project"))), h(s.get("host")),
                                              h(s.get("name") or s.get("title")))
                   for s in stale)
    return ('<details class="stale-fold" data-key="stale-sessions" data-collapse-narrow>'
            '<summary>另有 %d 个超过 1 天没有动静的后台会话</summary><ul>%s</ul></details>'
            % (len(stale), rows))


# ----------------------------------------------------------------- backlog

BACKLOG_PREVIEW = 5
OLD_DAYS = 30


def _bl_item(item):
    badge = ' <span class="bl-flag">%s</span>' % TOP_MARK if item.get("top") else ""  # board.is_top
    text = item.get("text")
    body = '<p class="bl-text muted">%s</p>' % h(text) if text and text != item.get("title") else ""
    box = "checked disabled" if item.get("done") else "disabled"
    return ('<li class="bl-item"><input type="checkbox" %s>'
            '<div><span class="bl-title">%s</span>%s%s</div></li>'
            % (box, h(item.get("title")), badge, body))


def _bl_grouped(items):
    """Items grouped by section, in the order the sections first appear."""
    order, groups = [], {}
    for it in items:
        sec = it.get("section")
        if sec not in groups:
            groups[sec] = []
            order.append(sec)
        groups[sec].append(it)
    out = []
    for sec in order:
        if sec:
            out.append('<h4 class="bl-section">%s</h4>' % h(sec))
        out.append('<ul class="bl-list">%s</ul>' % "".join(_bl_item(i) for i in groups[sec]))
    return "".join(out)


def _bl_updated(updated, now):
    if not updated:
        return "", ""
    days = int(max(0, now - updated) // 86400)
    when = time.strftime("%m-%d", time.localtime(updated))
    text = "今天更新" if days == 0 else "%d 天前更新（%s）" % (days, when)
    return text, " old" if days > OLD_DAYS else ""


def _backlog_card(b, now, show_host):
    items = [i for i in b.get("items") or () if isinstance(i, dict)]
    opened = [i for i in items if not i.get("done")]
    done = [i for i in items if i.get("done")]
    key = "backlog-%s-%s" % (b.get("host"), b.get("project"))
    upd, old = _bl_updated(b.get("updated"), now)
    host = '<span class="bl-host muted">%s · </span>' % h(b.get("host")) if show_host else ""
    out = ['<article class="card bl-card">',
           '<div class="bl-head"><span class="bl-project">%s%s</span>'
           '<span class="bl-count">%s 条 · 已完成 %s</span></div>'
           % (host, h(project_name(b.get("project"))), h(b.get("open") or 0), h(b.get("done") or 0))]
    if upd:
        out.append('<p class="bl-updated%s">%s</p>' % (old, h(upd)))
    if not items and b.get("raw"):
        out.append('<p class="muted">解析不了这份待办文件，原文如下：</p><pre class="bl-raw">%s</pre>'
                   % h(b.get("raw")))
    else:
        out.append('<ul class="bl-list">%s</ul>'
                   % "".join(_bl_item(i) for i in opened[:BACKLOG_PREVIEW]))
        if len(opened) > BACKLOG_PREVIEW:
            out.append('<details data-key="%s" data-collapse-narrow><summary>展开全部 %d 条</summary>%s'
                       '</details>' % (h(key + "-more"), len(opened),
                                       _bl_grouped(opened[BACKLOG_PREVIEW:])))
        if done:
            out.append('<details data-key="%s" data-collapse-narrow><summary>已完成 %d 条</summary>%s'
                       '</details>' % (h(key + "-done"), len(done), _bl_grouped(done)))
    out.append("</article>")
    return "".join(out)


def render_backlog(board, now=None):
    now = _now(board, now)
    backlogs = board.get("backlogs") or []
    multi_host = len({b.get("host") for b in backlogs}) > 1
    cards = "".join(_backlog_card(b, now, multi_host) for b in backlogs)
    fresh = []
    for hst in board.get("hosts") or ():
        if hst.get("collect_at"):
            fresh.append("%s 读取于 %s 前" % (hst.get("name"), span(now - hst["collect_at"])))
    missing = (board.get("summary") or {}).get("no_backlog") or []
    mnames = [project_name(m.get("project")) for m in missing]
    shown = ["%s · %s" % (m.get("host"), n) if mnames.count(n) > 1 else n
             for m, n in zip(missing, mnames)]
    return ('<section id="backlog"><a id="todo"></a><div class="bl-title-row"><h2>待办</h2>'
            '<span class="muted">读自各项目 docs/backlog.md · %s</span></div>'
            '<p class="muted">勾选框第二期启用：勾选后派给该项目的 Claude</p>'
            '<div class="bl-grid">%s</div>'
            '<p class="muted bl-none">没有待办文件：%s</p></section>'
            % (h(" · ".join(fresh) or "还没有读取"),
               cards or '<p class="muted">没有待办</p>', h(" · ".join(shown) or "无")))


# ------------------------------------------------------------------ header

def _host_chip(hst, now):
    at = hst.get("live_at") or hst.get("collect_at")
    age = max(0, now - at) if at else None
    name = hst.get("name")
    if hst.get("error") or hst.get("stale_since"):
        err = hst.get("error") or "同步失败"
        text = ("%s %d 分钟前的数据：%s" % (name, int(age // 60), err) if age is not None
                else "%s 还没有数据：%s" % (name, err))
        cls = "dot-err"
    elif hst.get("source") == "none":
        text, cls = "%s 活会话来源不可用，会话状态未知" % name, "dot-err"
    elif age is None:
        text, cls = "%s 还没有数据" % name, "dot-warn"
    elif age <= 30:
        text, cls = "%s 实时" % name, "dot-ok"
    else:
        text, cls = "%s %s 前采集" % (name, span(age)), "dot-warn" if age > 300 else "dot-ok"
    return '<span class="host"><svg class="dot %s" width="8" height="8" viewBox="0 0 8 8" ' \
           'aria-hidden="true"><circle cx="4" cy="4" r="4"/></svg>%s</span>' % (cls, h(text))


HOST_ERRORS_MAX = 5


def _host_errors(hosts):
    """Errors the hosts' live collection reported (git failures, unreadable transcripts...)."""
    errs = [(x.get("name"), e) for x in hosts for e in x.get("errors") or ()]
    if not errs:
        return ""
    items = "".join('<li>⚠ %s：%s</li>' % (h(n), h(e)) for n, e in errs[:HOST_ERRORS_MAX])
    if len(errs) > HOST_ERRORS_MAX:
        items += '<li>另有 %d 条</li>' % (len(errs) - HOST_ERRORS_MAX)
    return '<ul class="host-errors muted">%s</ul>' % items


def _zellij_exited(summary):
    rows = summary.get("zellij_exited") or []
    n = summary.get("zellij_exited_n", len(rows))
    if not n:
        return ""
    lines = "".join('<li><code>%s</code> <span class="muted">%s</span></li>'
                    % (h(r.get("cmd")), h(r.get("host"))) if r.get("cmd")
                    else '<li>%s <span class="muted">%s</span></li>' % (h(UNSAFE_NAME), h(r.get("host")))
                    for r in rows)
    return ('<details class="zellij-exited" data-key="zellij-exited" data-collapse-narrow>'
            '<summary>已退出的 zellij 会话 %s 个</summary><ul>%s</ul>'
            '<p class="muted">清理命令由你自己执行；proj 不会自动删除你自己起名的会话。</p></details>'
            % (h(n), lines))


def render_header(board, now=None):
    now = _now(board, now)
    summary = board.get("summary") or {}
    waiting = summary.get("waiting", len(board.get("waiting") or ()))
    busy = summary.get("busy", len(board.get("busy") or ()))
    idle = summary.get("idle", len(board.get("idle") or ()))
    todos, projects = summary.get("open_todos", 0), summary.get("todo_projects", 0)
    idle_note = '<span class="counter-note muted">◌ 空闲 %s</span>' % h(idle) if idle else ""
    chips = "".join(_host_chip(x, now) for x in board.get("hosts") or ())
    return ('<div class="page-head"><div><h1>proj 看板</h1><div class="hosts">%s'
            '<span class="muted">每 5 秒刷新 · %s</span></div>%s%s%s</div>'
            '<div class="counters">'
            '<a class="counter counter-wait" href="#wait"><b>%s</b>等你回复</a>'
            '<a class="counter counter-busy" href="#busy"><b>%s</b>进行中%s</a>'
            '<a class="counter counter-todo" href="#todo"><b>%s</b>条待办 · %s 个项目</a>'
            '</div></div>'
            % (chips, h(_clock(now)), _board_stale(board, now), _host_errors(board.get("hosts") or ()),
               _zellij_exited(summary),
               h(waiting), h(busy), idle_note,
               h(todos), h(projects)))


# ---------------------------------------------------------------- projects

HEAT_DAYS = 14
TILE_STATE = {"wait": "等你", "busy": "进行中", "none": "没有会话在跑", "unknown": "状态未知"}


def _heat_level(n):
    return 0 if n <= 0 else 1 if n < 3 else 2 if n < 10 else 3 if n < 30 else 4


def _heat(bins):
    """14 cells, oldest first; None (git unreadable) draws empty cells, not zeros."""
    cells = list(bins) if isinstance(bins, (list, tuple)) else [None] * HEAT_DAYS
    cells = (cells + [None] * HEAT_DAYS)[:HEAT_DAYS] if bins else [None] * HEAT_DAYS
    rects = []
    for i, n in enumerate(cells):
        if not isinstance(n, int):
            cls, title = "heat heat-empty", "读取失败"
        else:
            cls, title = "heat heat-%d" % _heat_level(n), "%d 次提交" % n
        rects.append('<rect class="%s" x="%d" y="0" width="12" height="12" rx="2"><title>%s</title></rect>'
                     % (cls, i * 15, h(title)))
    return ('<svg class="heat-row" viewBox="0 0 %d 12" width="%d" height="12" role="img" '
            'aria-label="最近 14 天的提交">%s</svg>' % (HEAT_DAYS * 15 - 3, HEAT_DAYS * 15 - 3, "".join(rects)))


def _tile(t, show_host):
    state = t.get("state") if t.get("state") in TILE_STATE else "none"
    host = '<span class="tile-host muted">%s · </span>' % h(t.get("host")) if show_host else ""
    todo = t.get("todo") or 0
    tasks = [{"status": st} for st in t.get("tasks") or ()]
    bar = _task_bar(tasks) if tasks else ""
    return ('<article class="tile tile-%s"><div class="tile-head"><span class="tile-name">%s%s</span>'
            '<span class="tile-todo">☐%s</span></div><p class="tile-state">%s</p>%s'
            '<p class="tile-text">%s</p>%s</article>'
            % (state, host, h(project_name(t.get("project"))), h(todo), h(TILE_STATE[state]), bar,
               h(t.get("text")), _heat(t.get("commits_14d"))))


def render_projects(board, now=None):
    now = _now(board, now)
    tiles = board.get("tiles") or []
    multi = len({t.get("host") for t in tiles}) > 1
    head = ('<div class="page-head"><div><h1>项目</h1><span class="muted">最近 14 天有动静、有待办或被标记的项目 · '
            '每格是一天的提交数</span>%s</div></div>' % _board_stale(board, now))
    legend = ('<ul class="legend">%s</ul>' % "".join(
        '<li><svg width="12" height="12" viewBox="0 0 12 12"><rect class="heat heat-%d" width="12" '
        'height="12" rx="2"/></svg> %s</li>' % (i, lab)
        for i, lab in enumerate(("0", "1–2", "3–9", "10–29", "30+"))))
    wall = "".join(_tile(t, multi) for t in tiles) or '<p class="muted">还没有项目</p>'
    return '%s<section id="tiles"><div class="tile-wall">%s</div>%s</section>' % (head, wall, legend)


# -------------------------------------------------------------------- page

NAV = '<nav><a href="/">看板</a><a href="/projects">项目</a></nav>'


def render(board, page, fragment=False, now=None):
    """now: the request time (serve passes it); None renders the board as of its own time."""
    now = _now(board, now)
    if page == "projects":
        inner = render_projects(board, now)
        if fragment:
            return inner
        body = '<main data-poll="/fragment/projects" data-interval="5000">%s</main>' % inner
        return layout("proj 项目", NAV, body, ["/static/base.css", "/static/app.css"])
    if page != "home":
        raise ValueError("unknown page: %r" % (page,))
    inner = "\n".join(p for p in (render_header(board, now), render_timeline(board),
                                  render_columns(board, now), render_unknown_fold(board),
                                  render_stale_fold(board), render_backlog(board, now)) if p)
    if fragment:
        return inner
    body = '<main data-poll="/fragment/home" data-interval="5000">%s</main>' % inner
    return layout("proj 看板", NAV, body, ["/static/base.css", "/static/app.css"])
