import pytest

from projdeck import ui
from projdeck.ui import strip_ansi

NOW = 100 * 86400 + 12 * 3600


def row(path="/home/u/quant", host="s-host", **kw):
    r = {"host": host, "path": path, "exists": True, "local": False, "ssh": host, "home": "/home/u",
         "tier": "active", "activity": NOW - 3600, "stale_since": None, "last_mtime": 0, "git": None,
         "suspicious": [], "progress": "", "errors": [], "sessions": [], "tag": None, "flag": False}
    r.update(kw)
    return r


def tasks(n=8, done=6, doing=7):
    items = []
    for i in range(1, n + 1):
        st = "done" if i <= done else ("doing" if i == doing else "todo")
        items.append({"id": "T%02d" % i, "title": "task %d" % i, "status": st})
    return {"label": "run-x", "source": "plan", "current": "T%02d" % doing, "items": items}


def sess(state="busy", **kw):
    d = {"id": "s1", "host": "s-host", "project": "/home/u/quant", "state": state, "name": "quant-01",
         "title": "t", "started_at": NOW - (2 * 3600 + 55 * 60), "idle_since": NOW - 600,
         "segments": [["busy", NOW - 7200, NOW - 3600], ["sub", NOW - 3600, NOW - 1800],
                      ["wait", NOW - 1800, NOW]],
         "tasks": tasks(), "tasks_owner": None, "question": "", "last_reply": "all done", "errors": []}
    d.update(kw)
    return d


def lines_of(text):
    return [strip_ansi(x) for x in text.splitlines()]


def test_preview_timeline_and_full_task_list():
    out = lines_of(ui.preview_text(row(), NOW, sess()))
    i = next(k for k, x in enumerate(out) if "最近 6 小时" in x)
    assert out[i + 1] == "▆" * 48
    assert out[i + 2].startswith("Claude 1h00m · 子代理 30m · 闲置等输入 30m")
    j = next(k for k, x in enumerate(out) if x.startswith("── 计划 run-x · 6/8 ──"))
    assert out[j + 1:j + 9] == ["✓ T01 task 1", "✓ T02 task 2", "✓ T03 task 3", "✓ T04 task 4",
                                "✓ T05 task 5", "✓ T06 task 6", "◉ T07 task 7", "○ T08 task 8"]
    assert any("◉ 运行中 2h55m" in x for x in out)
    assert "── Claude 最后说 ──" in out and out[out.index("── Claude 最后说 ──") + 1] == "all done"


def test_waiting_line_and_stuck_marker():
    t = tasks()
    t["items"][6]["status"] = "stuck"
    out = lines_of(ui.preview_text(row(), NOW, sess("waiting", tasks=t)))
    assert any(x.startswith("◆ 等你 10m") for x in out)
    assert "! T07 task 7" in out


def test_last_reply_capped_at_300():
    out = lines_of(ui.preview_text(row(), NOW, sess(last_reply="x" * 500)))
    reply = out[out.index("── Claude 最后说 ──") + 1]
    assert len(reply) <= 300 and reply.startswith("xxx")


def test_session_errors_listed_with_project_errors():
    out = ui.preview_text(row(errors=["git: boom"]), NOW, sess(errors=["task file broken"]))
    tail = out[out.index("errors:"):]
    assert "git: boom" in tail and "task file broken" in tail


def test_live_none_is_unchanged_and_empty_live_adds_no_session_sections():
    r = row(sessions=[{"id": "a", "title": "T", "last_ts": NOW - 60, "recent": ["hi"],
                       "last_reply": "yo"}])
    base = ui.preview_text(r, NOW)
    assert base == ui.preview_text(r, NOW, None)
    assert "最近 6 小时" not in ui.preview_text(r, NOW, {})


def test_no_tasks_and_no_data_do_not_break():
    s = sess(tasks=None, segments=[], last_reply="", started_at=None, idle_since=None)
    out = lines_of(ui.preview_text(row(), NOW, s))
    i = next(k for k, x in enumerate(out) if "最近 6 小时" in x)
    assert out[i + 1] == "▆" * 48
    assert not any(x.startswith("── 计划") for x in out)


def test_uncollected_project_shows_hint_not_misleading_git_lines():
    s = sess()
    r = ui._minimal_row("s-host", "/home/u/new", s, NOW)
    out = lines_of(ui.preview_text(r, NOW, s))
    assert any("还没采集" in x and "^U" in x for x in out)
    assert any("最近 6 小时" in x for x in out)
    assert not any("directory no longer exists" in x or "not a git repo" in x for x in out)


# ---- backlog picker

def bl(items=(), raw=None, done=0):
    items = list(items)
    return {"path": "docs/backlog.md", "mtime": 5, "items": items, "raw": raw, "done": done,
            "open": sum(1 for i in items if not i["done"])}


def item(title, done=False, text=None):
    return {"title": title, "done": done, "section": "S", "text": text or ("body of " + title)}


@pytest.fixture
def fake_fzf(monkeypatch):
    got = {}

    def fake(lines, args, expect=()):
        got["lines"], got["args"] = lines, args
        pick = got.get("pick", lambda ls: next(l for l in ls if "second" in l))
        return "", [pick(lines)]
    monkeypatch.setattr(ui, "run_fzf", fake)
    return got


def test_pick_backlog_groups_open_items_and_returns_project(fake_fzf):
    rows = [row("/home/u/quant", backlog=bl([item("first"), item("second"), item("old", True)], done=1)),
            row("/home/u/other", host="em-host", backlog=bl([item("x")])),
            row("/home/u/none")]
    assert ui.pick_backlog(rows, NOW) == ("s-host", "/home/u/quant")
    shown = [strip_ansi(l.split("\t", 3)[3]) for l in fake_fzf["lines"]]
    assert "☐2 · 已完成 1（未显示）──" in shown[0]
    assert not any("old" in x for x in shown)
    assert not any("none" in x for x in shown)
    assert any("first" in x for x in shown) and any(x.strip() == "☐ x" for x in shown)


def test_pick_backlog_unparseable_project(fake_fzf):
    fake_fzf["pick"] = lambda ls: next(l for l in ls if "无法解析" in l)
    rows = [row("/home/u/raw", backlog=bl([], raw="free text\nnot a list"))]
    assert ui.pick_backlog(rows, NOW) == ("s-host", "/home/u/raw")
    assert any("（无法解析，预览看全文）" in l for l in fake_fzf["lines"])


def test_pick_backlog_cancel(monkeypatch):
    def cancel(lines, args, expect=()):
        raise ui.Cancelled()
    monkeypatch.setattr(ui, "run_fzf", cancel)
    with pytest.raises(ui.Cancelled):
        ui.pick_backlog([row(backlog=bl([item("a")]))], NOW)


def test_pick_backlog_preview_uses_item_index(fake_fzf):
    rows = [row(backlog=bl([item("done one", True), item("second")]))]
    ui.pick_backlog(rows, NOW)
    chosen = next(l for l in fake_fzf["lines"] if "second" in l)
    assert chosen.split("\t")[2] == "1"       # index into the full item list
    assert any(a.startswith("--preview=") and "_bpreview" in a for a in fake_fzf["args"])


def test_backlog_preview_text_item_and_raw():
    r = row(backlog=bl([item("a", text="line1\nline2"), item("b")]))
    out = ui.backlog_preview_text(r, 0)
    assert "line1\nline2" in out and "a" in out
    raw = row(backlog=bl([], raw="ORIGINAL TEXT"))
    assert "ORIGINAL TEXT" in ui.backlog_preview_text(raw, "")
    assert "ORIGINAL TEXT" in ui.backlog_preview_text(raw, 0)
    assert ui.backlog_preview_text(row(), 0)     # no backlog: still says something


# ---- review round 1

MARK = "\x1b[38;2;1;2;3m"      # a colour ui itself never emits
EVIL = MARK + "A\x1b]0;x\x07B\x1b]0;y\x1b\\C\rD\x9bE\x7fF\x1b(0G"
def stray_controls(text):
    """ESC / C1 / C0 characters left once ui's own SGR colouring is removed."""
    return [c for c in strip_ansi(text) if (ord(c) < 32 and c not in "\n\t") or 0x7f <= ord(c) <= 0x9f]


def test_safe_strips_sequences_and_controls():
    assert ui._safe(EVIL) == "ABCDEFG"
    assert ui._safe("a\tb\nc") == "a b\nc"
    assert ui._one_line("a\x1b[31m b\r\n c") == "a b c"


def test_untrusted_text_cannot_inject_escapes_anywhere():
    """One entry per external source that ui.py puts into output (see impl-report table)."""
    t = tasks()
    t["items"][0].update(id=MARK + "T01", title=EVIL)
    t["label"] = t["path"] = EVIL
    t["current"] = EVIL                                       # task_bar.current
    s = sess("busy", tasks=t, last_reply=EVIL, question=EVIL, title=EVIL, errors=[EVIL],
             attach="zellij attach " + EVIL)
    w = sess("waiting", question=EVIL, tasks=None)
    r = row(path="/home/u/" + EVIL, host=EVIL, home="/home/u", errors=[EVIL], progress=EVIL,
            suspicious=[EVIL], tag=EVIL, live_zellij=[{"name": EVIL, "claude": 1}],
            live_tmux=[EVIL],
            sessions=[{"id": "i", "title": EVIL, "last_ts": NOW - 60, "recent": [EVIL],
                       "last_reply": EVIL}],
            backlog=bl([item(EVIL, text=EVIL)]))
    raw = row(backlog=bl([], raw=EVIL))
    s16 = sess("busy", tasks=dict(t, items=items16()))
    outs = [ui.preview_text(r, NOW, s), ui.preview_text(r, NOW, w), ui.preview_text(r, NOW),
            ui.backlog_preview_text(r, 0), ui.backlog_preview_text(raw, 0),
            ui.backlog_preview_text(r, ""),
            ui.session_preview_text(r, r["sessions"][0], NOW, EVIL),
            ui.project_line(r, NOW, live=s), ui.project_line(r, NOW, live=w),
            ui.project_line(r, NOW, live=s16), ui.project_line(r, NOW, show_host=True),
            ui.project_line(r, NOW, live=dict(s, age=900)),
            ui.session_line(r, r["sessions"][0], NOW),
            ui.prompt_line(r, r["sessions"][0], EVIL, NOW),
            ui.host_preview_text(EVIL, [{"host": EVIL, "stale_since": 1, "last_error": EVIL}], [r], NOW),
            ui.live_flag(r), ui.file_line({"path": EVIL, "suspicious": True, "size": 1}),
            ui.tag_legend([EVIL])]
    outs += ui.backlog_lines([r]) + ui.project_lines([r], NOW)
    outs += ui.project_lines([r], NOW, {"hosts": [{"name": EVIL, "live_at": NOW}], "summary": {},
                                          "waiting": [dict(w, host=EVIL, project=r["path"])],
                                          "busy": [], "idle": []})
    outs += ui.header_lines({"hosts": [{"name": EVIL, "live_at": NOW}], "summary": {}}, NOW)
    outs.append(ui.task_bar(dict(t, items=t["items"])))
    for o in outs:
        o = o.split("\t")[-1]        # fzf lines: host / path / id fields are data, the last is shown
        assert MARK not in o, o
        assert stray_controls(o) == [], o
        assert "\x1b]" not in o and "\x1b(" not in o and "\x9b" not in o


def items16():
    return [{"id": MARK + "T%02d" % i, "title": "t", "status": "doing" if i == 1 else "todo"}
            for i in range(1, 20)]


def test_pick_live_and_notice_and_batch_header_are_safe(monkeypatch):
    seen = {}

    def fake(lines, args, expect=()):
        seen.update(lines=lines, args=args)
        return "", [lines[0]]
    monkeypatch.setattr(ui, "run_fzf", fake)
    ui.pick_live([{"name": EVIL, "claude": 1}], EVIL)
    assert MARK not in "".join(seen["lines"]) + "".join(seen["args"])
    ui.pick_project([row()], NOW, notice=EVIL)
    assert MARK not in "".join(seen["args"])


def test_osc_does_not_eat_following_text():
    assert ui._safe("keep \x1b]0;t\x9c this line\nand next") == "keep  this line\nand next"
    assert ui._safe("a\x1b]0;unterminated\nnext line") == "a\nnext line"
    assert ui._safe("a\x1b]0;t\x1b\\b\x1b]0;t\x07c") == "abc"


def test_zellij_name_still_shown_when_status_line_lacks_it():
    r = row(live_zellij=[{"name": "multi", "claude": 1}])
    for live in (sess("waiting"), sess("idle"), sess("busy", age=900)):
        out = lines_of(ui.preview_text(r, NOW, live))
        assert any("multi" in x for x in out), live["state"]


def test_missing_idle_since_shows_dash_not_zero():
    for state, word in (("waiting", "等你"), ("idle", "空闲")):
        out = lines_of(ui.preview_text(row(), NOW, sess(state, idle_since=None)))
        assert out[1].endswith("%s —" % word), out[1]
    tail = strip_ansi(ui.project_line(row(), NOW, live=sess("idle", idle_since=None)))
    assert tail.endswith("空闲 —")


def test_plan_heading_without_label_falls_back_to_path():
    t = tasks()
    t["label"] = None
    t["path"] = "docs/plan.md"
    out = lines_of(ui.preview_text(row(), NOW, sess(tasks=t)))
    assert "── 计划 docs/plan.md · 6/8 ──" in out
    t["path"] = None
    out = lines_of(ui.preview_text(row(), NOW, sess(tasks=t)))
    assert "── 计划 · 6/8 ──" in out


def test_zellij_shown_once_and_status_line_composed():
    r = row(live_zellij=[{"name": "multi", "claude": 1}])
    out = lines_of(ui.preview_text(r, NOW, sess()))
    assert out[1] == "◉ 运行中 2h55m · zellij multi（1 claude）"
    assert sum("zellij" in x for x in out) == 1


def test_clip_total_is_shared_with_board():
    from projdeck import board
    assert hasattr(board, "clip_total") and not hasattr(ui, "_totals")
