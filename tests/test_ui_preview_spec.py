"""Independent spec tests for T27: terminal preview (ui.preview_text with live) and the
^L backlog picker (ui.pick_backlog / ui.backlog_preview_text)."""
import calendar
import re
import time

import pytest

from projdeck import board as board_mod
from projdeck import ui

DAY0 = calendar.timegm((2026, 11, 18, 0, 0, 0))
NOW = DAY0 + 23 * 3600
CFG = {"hosts": [{"name": "loc", "local": True}, {"name": "s-host", "ssh": "s-host"},
                 {"name": "em", "ssh": "em"}]}
C_BUSY, C_EMPTY = (57, 135, 229), (42, 47, 55)
SGR = re.compile(r"\x1b\[([0-9;]*)m")


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def plain(s):
    return ui.strip_ansi(s)


def row(host="s-host", path="/srv/p1", **kw):
    r = {"host": host, "path": path, "exists": True, "local": host == "loc", "ssh": host,
         "tier": "active", "activity": NOW - 3600, "stale_since": None, "last_mtime": 0,
         "git": None, "suspicious": [], "progress": "", "errors": [], "sessions": [],
         "tag": None, "flag": False, "tags": []}
    r.update(kw)
    return r


def sess(state="busy", project="/srv/p1", **kw):
    d = {"id": "s1", "project": project, "cwd": project, "latest_cwd": project,
         "kind": "interactive", "live": True, "name": "n-s1", "title": "", "host": "s-host",
         "started_at": NOW - 10500, "status": "busy" if state != "waiting" else "idle",
         "state": state, "idle_since": NOW - 9360 if state == "waiting" else None,
         "question": "", "last_reply": "", "last_human": {"ts": 0, "text": ""},
         "last_input": "human", "subagents_running": 0, "segments": [], "attach": "",
         "tasks": None, "tasks_owner": None, "errors": []}
    d.update(kw)
    return d


def tasks(statuses, label="myplan", source="plan"):
    return {"source": source, "label": label, "path": "/x", "checkout": "/srv/p1",
            "current": None, "merges": [],
            "items": [{"id": "T%02d" % (i + 1), "title": "title%d" % (i + 1), "status": s,
                       "note": ""} for i, s in enumerate(statuses)]}


def blocks_colors(raw):
    out, cur = [], None
    for m in re.finditer(r"\x1b\[([0-9;]*)m|[▆▒]", raw):
        if m.group(0) in ("▆", "▒"):
            out.append(cur)
        else:
            mm = re.search(r"38;2;(\d+);(\d+);(\d+)", m.group(1))
            if mm:
                cur = tuple(int(x) for x in mm.groups())
            elif m.group(1) in ("", "0"):
                cur = None
    return out


def lines_of(text):
    return plain(text).split("\n")


def idx_of(lines, needle):
    hits = [i for i, ln in enumerate(lines) if needle in ln]
    assert hits, (needle, lines)
    return hits[0]


# ------------------------------------------------------------------ AC1: timeline + tasks

def full_session():
    segs = [["busy", NOW - 21600, NOW - 18000],        # 1h
            ["sub", NOW - 18000, NOW - 16200],         # 30m
            ["wait", NOW - 16200, NOW - 9000]]         # 2h
    return sess("busy", segments=segs,
                tasks=tasks(["done"] * 6 + ["doing", "todo"]),
                last_reply="all green so far")


def test_timeline_block_and_durations_follow_header():
    text = ui.preview_text(row(), NOW, live=full_session())
    ls = lines_of(text)
    h = idx_of(ls, "最近 6 小时")
    assert "17:00" in ls[h] and "23:00" in ls[h]
    assert ls[h + 1].count("▆") == 48
    assert re.search(r"Claude 1h\S* · 子代理 30m · 闲置等输入 2h", ls[h + 2])


def test_timeline_cell_colors_from_segments():
    # busy for the first 3h of the 6h window, nothing afterwards
    s = sess("busy", segments=[["busy", NOW - 21600, NOW - 10800]])
    text = ui.preview_text(row(), NOW, live=s)
    raw = text.split("\n")
    h = [i for i, ln in enumerate(raw) if "最近 6 小时" in plain(ln)][0]
    assert blocks_colors(raw[h + 1]) == [C_BUSY] * 24 + [C_EMPTY] * 24


def test_durations_ignore_segment_time_outside_window():
    base = full_session()
    extra = sess("busy", segments=base["segments"] + [["busy", NOW - 40000, NOW - 30000]],
                 tasks=base["tasks"])
    a = lines_of(ui.preview_text(row(), NOW, live=base))
    b = lines_of(ui.preview_text(row(), NOW, live=extra))
    ha, hb = idx_of(a, "最近 6 小时"), idx_of(b, "最近 6 小时")
    assert a[ha + 2] == b[hb + 2]
    assert a[ha + 1] == b[hb + 1]


def test_full_task_list_with_markers_and_count():
    ls = lines_of(ui.preview_text(row(), NOW, live=full_session()))
    t = [i for i, ln in enumerate(ls) if re.fullmatch(r"── 计划 myplan · 6/8 ──", ln.strip())]
    assert len(t) == 1
    body = [ln for ln in ls[t[0] + 1:t[0] + 9]]
    assert len(body) == 8
    for n, ln in enumerate(body, 1):
        assert "T%02d" % n in ln and "title%d" % n in ln
    assert all(ln.strip().startswith("✓") for ln in body[:6])
    assert body[6].strip().startswith("◉") and body[7].strip().startswith("○")
    # order: timeline < plan < Claude says
    assert idx_of(ls, "最近 6 小时") < t[0] < idx_of(ls, "── Claude 最后说 ──")


def test_stuck_task_marked_bang_and_not_counted():
    s = sess("busy", tasks=tasks(["done", "stuck", "todo"]))
    ls = lines_of(ui.preview_text(row(), NOW, live=s))
    assert any("── 计划 myplan · 1/3 ──" in ln for ln in ls)
    stuck = [ln for ln in ls if "T02" in ln][0]
    assert stuck.strip().startswith("!")


def test_no_tasks_means_no_plan_section():
    ls = lines_of(ui.preview_text(row(), NOW, live=sess("busy", tasks=None)))
    assert not any("── 计划" in ln for ln in ls)


# ------------------------------------------------------------------ AC2: state line, last said, errors

def test_running_line_uses_now_minus_started_at():
    ls = lines_of(ui.preview_text(row(), NOW, live=sess("busy", started_at=NOW - 10500)))
    assert any("◉ 运行中 2h55m" in ln for ln in ls)
    assert idx_of(ls, "◉ 运行中") < idx_of(ls, "最近 6 小时")


def test_sub_state_also_running():
    ls = lines_of(ui.preview_text(row(), NOW, live=sess("sub", started_at=NOW - 600)))
    assert any("◉ 运行中 10m" in ln for ln in ls)


def test_waiting_line_uses_now_minus_idle_since():
    s = sess("waiting", started_at=NOW - 50000, idle_since=NOW - 9360, question="which db?")
    text = "\n".join(lines_of(ui.preview_text(row(), NOW, live=s)))
    assert "◆ 等你 2h36m" in text
    assert "◉ 运行中" not in text


def test_last_reply_under_claude_said_heading():
    ls = lines_of(ui.preview_text(row(), NOW, live=full_session()))
    i = idx_of(ls, "── Claude 最后说 ──")
    assert "all green so far" in "\n".join(ls[i + 1:i + 4])


def test_session_errors_listed_in_errors_section():
    s = sess("busy", errors=["bad transcript line 7"])
    r = row(errors=["git failed"])
    ls = lines_of(ui.preview_text(r, NOW, live=s))
    i = idx_of(ls, "errors:")
    tail = "\n".join(ls[i:])
    assert "bad transcript line 7" in tail
    assert "git failed" in tail


def test_errors_section_still_shows_project_errors_without_session_errors():
    ls = lines_of(ui.preview_text(row(errors=["git failed"]), NOW, live=sess("busy")))
    assert "git failed" in "\n".join(ls[idx_of(ls, "errors:"):])


def test_live_none_is_unchanged_and_has_no_new_sections():
    r = row(git={"branch": "main", "dirty": 2, "untracked": 1, "ahead": 1, "no_upstream": False,
                 "last_commit_ts": NOW - 86400}, suspicious=["debug.log"],
            progress="step 1\nstep 2", errors=["e1"])
    a = ui.preview_text(r, NOW)
    assert a == ui.preview_text(r, NOW, live=None)
    for needle in ("最近 6 小时", "运行中", "◆ 等你", "Claude 最后说", "── 计划"):
        assert needle not in a
    for needle in ("main", "±2", "debug.log", "step 2", "e1", "errors:"):
        assert needle in a


def test_live_adds_content_on_top_of_plain_preview():
    r = row(progress="step 1\nstep 2")
    plain_txt = ui.preview_text(r, NOW)
    with_live = ui.preview_text(r, NOW, live=full_session())
    assert len(with_live) > len(plain_txt)
    assert "step 2" in with_live


# ------------------------------------------------------------------ uncollected minimal row

def uncollected_row():
    return row(path="/srv/newproj", uncollected=True, git=None, sessions=[], progress="",
               suspicious=[])


def test_uncollected_row_shows_session_state_and_hint():
    b = board_mod.build([{"schema": 1, "host": "s-host", "collected_at": NOW - 60, "synced_at": NOW - 60,
                          "stale_since": None, "last_error": None, "projects": []}],
                        [{"schema": 1, "host": "s-host", "collected_at": NOW - 30, "source": "x",
                          "since": NOW - 86400, "projects": {},
                          "sessions": [sess("waiting", project="/srv/newproj", question="ok?")]}],
                        CFG, NOW)
    s = b["waiting"][0]
    text = ui.preview_text(uncollected_row(), NOW, live=s)
    assert text.strip()
    flat = text.replace("，", ",")
    assert "该项目还没采集,按 ^U 同步" in plain(flat)
    assert "◆ 等你 2h36m" in plain(text)
    assert "最近 6 小时" in plain(text)


def test_uncollected_without_live_still_renders():
    # live=None must stay identical to the old output; just must not crash or go blank
    text = ui.preview_text(uncollected_row(), NOW)
    assert text.strip() and "None" not in text


def test_collected_row_has_no_uncollected_hint():
    text = ui.preview_text(row(), NOW, live=sess("busy"))
    assert "还没采集" not in text


# ------------------------------------------------------------------ None handling and 300 cap

def test_none_fields_render_without_none_or_zero():
    cases = [sess("waiting", question=None, idle_since=None, last_reply=None, tasks=None,
                  segments=None, errors=None, last_human=None, started_at=None),
             sess("busy", started_at=None, tasks=None, segments=None, last_reply=None,
                  title=None, errors=None)]
    for s in cases:
        text = ui.preview_text(row(), NOW, live=s)
        assert "None" not in text, s["state"]
        assert not re.search(r"(等你|运行中) 0[smh]", plain(text)), s["state"]
        state_line = [ln for ln in lines_of(text) if ln.startswith(("◆", "◉"))][0]
        assert "—" in state_line and not re.search(r"\b0[smh]\b", state_line), state_line
        ls = lines_of(text)
        assert ls[idx_of(ls, "最近 6 小时") + 1].count("▆") == 48


def test_session_text_capped_at_300_chars():
    s = sess("waiting", question="q" * 600, last_reply="r" * 600)
    text = plain(ui.preview_text(row(), NOW, live=s))
    assert "q" * 301 not in text and "r" * 301 not in text
    assert "r" * 50 in text


def test_short_text_not_truncated():
    s = sess("busy", last_reply="r" * 300)
    assert "r" * 300 in plain(ui.preview_text(row(), NOW, live=s))


# ------------------------------------------------------------------ AC3: pick_backlog

def item(title, done=False, text=None):
    return {"title": title, "done": done, "section": None,
            "text": text if text is not None else "- " + title + "\n  body of " + title}


def brow(host, path, items, raw=None, **kw):
    n_done = sum(1 for i in items if i["done"])
    return row(host=host, path=path,
               backlog={"path": "docs/backlog.md", "mtime": NOW - 100, "items": items,
                        "open": len(items) - n_done, "done": n_done, "raw": raw}, **kw)


def run_pick(monkeypatch, rows, choose=None, cancel=False):
    cap = {}

    def fake(lines, args, expect=()):
        cap["lines"], cap["args"] = list(lines), list(args)
        if cancel:
            raise ui.Cancelled()
        sel = [ln for ln in lines if choose in plain(ln)]
        assert sel, (choose, lines)
        return "", [sel[0]]
    monkeypatch.setattr(ui, "run_fzf", fake)
    try:
        res = ui.pick_backlog(rows, NOW)
    except ui.Cancelled:
        res = ui.Cancelled
    return res, cap


def rows_basic():
    return [brow("s-host", "/srv/alpha", [item("first open"), item("old done", True),
                                      item("second open")]),
            brow("s-host", "/srv/beta", [], raw="free form text here")]


def test_groups_headers_and_open_items_only(monkeypatch):
    res, cap = run_pick(monkeypatch, rows_basic(), choose="first open")
    shown = [plain(ln) for ln in cap["lines"]]
    heads = [ln for ln in shown if "── " in ln and "alpha" in ln]
    assert len(heads) == 1
    assert re.search(r"── .*alpha.* ☐2 · 已完成 1（未显示）──", heads[0])
    blob = "\n".join(shown)
    assert "first open" in blob and "second open" in blob
    assert "old done" not in blob
    # items sit under their header
    hi = shown.index(heads[0])
    assert hi < [i for i, ln in enumerate(shown) if "first open" in ln][0]
    assert hi < [i for i, ln in enumerate(shown) if "second open" in ln][0]


def test_unparsable_project_shows_single_notice_line(monkeypatch):
    res, cap = run_pick(monkeypatch, rows_basic(), choose="first open")
    shown = [plain(ln) for ln in cap["lines"]]
    assert sum("（无法解析，预览看全文）" in ln for ln in shown) == 1
    # good project has no such notice
    ai = [i for i, ln in enumerate(shown) if "alpha" in ln and "── " in ln][0]
    ni = [i for i, ln in enumerate(shown) if "（无法解析，预览看全文）" in ln][0]
    assert ni > ai
    assert "free form text here" in plain(ui.backlog_preview_text(rows_basic()[1], 0))


def test_selecting_item_returns_host_and_path(monkeypatch):
    res, _ = run_pick(monkeypatch, rows_basic(), choose="second open")
    assert res == ("s-host", "/srv/alpha")


def test_selecting_notice_line_returns_that_project(monkeypatch):
    res, _ = run_pick(monkeypatch, rows_basic(), choose="（无法解析，预览看全文）")
    assert res == ("s-host", "/srv/beta")


def test_cancel_raises_cancelled(monkeypatch):
    res, _ = run_pick(monkeypatch, rows_basic(), cancel=True)
    assert res is ui.Cancelled


def test_preview_wired_to_bpreview(monkeypatch):
    _, cap = run_pick(monkeypatch, rows_basic(), choose="first open")
    blob = " ".join(cap["args"])
    assert "--preview" in blob and "_bpreview" in blob


def test_projects_without_backlog_not_listed(monkeypatch):
    rows = rows_basic() + [row(host="s-host", path="/srv/nobacklog")]
    _, cap = run_pick(monkeypatch, rows, choose="first open")
    assert "nobacklog" not in plain("\n".join(cap["lines"]))


def test_group_title_gets_host_only_when_multi_host(monkeypatch):
    single = [brow("s-host", "/srv/alpha", [item("one")])]
    _, cap = run_pick(monkeypatch, single, choose="one")
    assert "@s-host" not in plain("\n".join(cap["lines"]))
    multi = [brow("s-host", "/srv/alpha", [item("one")]), brow("em", "/srv/gamma", [item("two")])]
    res, cap = run_pick(monkeypatch, multi, choose="two")
    shown = [plain(ln) for ln in cap["lines"] if "── " in plain(ln)]
    assert any("alpha" in ln and "@s-host" in ln for ln in shown)
    assert any("gamma" in ln and "@em" in ln for ln in shown)
    assert res == ("em", "/srv/gamma")


# ------------------------------------------------------------------ AC4: backlog_preview_text

def test_backlog_preview_gives_item_body():
    r = brow("s-host", "/srv/alpha", [item("first open"), item("second open",
             text="- second open\n  line two of body\n  line three")])
    out = plain(ui.backlog_preview_text(r, 1))
    assert "line two of body" in out and "line three" in out
    assert "body of first open" not in out


def test_backlog_preview_unparsable_gives_raw():
    r = brow("s-host", "/srv/beta", [], raw="# notes\nfree form text here\nmore")
    out = plain(ui.backlog_preview_text(r, 0))
    assert "free form text here" in out and "more" in out


def test_backlog_preview_returns_str_for_empty_backlog():
    r = brow("s-host", "/srv/empty", [], raw=None)
    out = ui.backlog_preview_text(r, 0)
    assert isinstance(out, str) and out.strip()


def test_idle_session_missing_idle_since_shows_dash():
    s = sess("idle", idle_since=None, started_at=None)
    text = plain(ui.preview_text(row(), NOW, live=s))
    state = [ln for ln in text.split("\n") if "空闲" in ln]
    assert state and all("—" in ln and not re.search(r"\b0[smh]\b", ln) for ln in state), text


# ------------------------------------------------------------------ untrusted text sanitising

BAD = ["\x1b[31m", "\x1b]0;x\x07", "\x9b", "\x1b"]


def no_raw_escapes(text, allow_sgr=True):
    """Strip the SGR colours the UI itself emits, then nothing control-ish may remain."""
    t = SGR.sub("", text) if allow_sgr else text
    assert "\x1b" not in t and "\x9b" not in t and "\x07" not in t, repr(t)


def test_preview_text_sanitises_untrusted_text():
    evil = "A\x1b[31mRED\x1b]0;x\x07B\x9b31mC"
    s = sess("busy", last_reply=evil, tasks=tasks(["doing"], label="plan"),
             errors=[evil])
    s["tasks"]["items"][0]["title"] = evil
    text = ui.preview_text(row(errors=[evil]), NOW, live=s)
    no_raw_escapes(text)
    assert "A" in text and "RED" in text  # content survives, only control bytes go


def test_backlog_lines_sanitise_untrusted_text(monkeypatch):
    evil = "T\x1b[31mX\x1b]0;x\x07Y\x9b31mZ"
    rows = [brow("s-host", "/srv/alpha", [item(evil)])]
    _, cap = run_pick(monkeypatch, rows, choose="TX")
    no_raw_escapes("\n".join(cap["lines"]))
    assert any("T" in ln and "X" in ln for ln in cap["lines"])
