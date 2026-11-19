"""Independent spec tests for T20: web.render_timeline / web.render_columns."""
import os
import re
from html.parser import HTMLParser

import pytest

from projdeck import board as board_mod
from projdeck import web

NOW = 1_800_000_000
EVIL = '<script>alert(1)</script><img src=x onerror=1>'
EVIL2 = '"onerror=1>javascript:alert(1)'


def home():
    return os.path.expanduser("~")


def sess(sid, state="busy", **kw):
    s = {"id": sid, "project": home() + "/dev/projdeck", "host": "s-host", "name": "name-" + sid,
         "title": "title-" + sid, "state": state, "status": "busy", "started_at": NOW - 7200,
         "idle_since": None, "question": "", "last_reply": "", "last_human": None,
         "last_input": "human", "subagents_running": 0, "segments": [], "attach": "zellij attach z-" + sid,
         "tasks": None, "tasks_owner": None, "errors": []}
    s.update(kw)
    return s


def mkboard(waiting=(), busy=(), idle=(), ended=(), rows=(), more=0, top=()):
    return {"hosts": [], "summary": {}, "waiting": list(waiting), "busy": list(busy),
            "idle": list(idle), "ended_today": list(ended), "stale": [], "stale_host_sessions": [],
            "lanes": {"start": NOW - 3 * 3600, "end": NOW, "rows": list(rows), "more": more},
            "top_todos": list(top), "backlogs": [], "tiles": []}


def row(sid="a", segs=(), commits=(), merges=(), state="busy", busy_s=0, wait_s=0, project=None):
    return {"host": "s-host", "project": project or home() + "/dev/projdeck", "session_id": sid,
            "state": state, "segments": [list(x) for x in segs], "commits": list(commits),
            "merges": [list(m) for m in merges], "busy_s": busy_s, "wait_s": wait_s}


def tasks(statuses, current=None, owner_ids=None):
    items = [{"id": "T%02d" % (i + 1), "title": "title of task %d" % (i + 1), "status": st, "note": ""}
             for i, st in enumerate(statuses)]
    return {"source": "run", "label": "run", "path": "docs/runs/x/ledger.md", "checkout": "/x",
            "current": current, "items": items, "merges": []}


class Dom(HTMLParser):
    """Tiny tree: records tags with attrs, ancestors and text."""

    def __init__(self, s):
        super().__init__(convert_charrefs=True)
        self.nodes, self.stack = [], []
        self.feed(s)

    def handle_starttag(self, tag, attrs):
        n = {"tag": tag, "attrs": dict(attrs), "anc": list(self.stack), "text": "", "id": len(self.nodes)}
        self.nodes.append(n)
        if tag not in ("rect", "line", "path", "circle", "polygon", "br", "img", "input", "use", "ellipse"):
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        n = {"tag": tag, "attrs": dict(attrs), "anc": list(self.stack), "text": "", "id": len(self.nodes)}
        self.nodes.append(n)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        for n in self.stack:
            n["text"] += data

    def classes(self, n):
        return set((n["attrs"].get("class") or "").split())


def seg_rects(dom):
    out = []
    for n in dom.nodes:
        if n["tag"] == "rect":
            cl = dom.classes(n) & {"seg-busy", "seg-sub", "seg-wait", "seg-wait-now"}
            if cl:
                out.append((n, cl))
    return out


def has_title(dom, n):
    return any(m["tag"] == "title" and m["anc"] and m["anc"][-1] is not None
               and any(a["id"] == n["id"] for a in m["anc"]) for m in dom.nodes) or \
        any(m["tag"] == "title" and m["id"] == n["id"] + 1 for m in dom.nodes)


def num(v):
    return float(re.match(r"-?[\d.]+", v).group(0))


# ---------------------------------------------------------------- timeline

def test_timeline_section_wrapper():
    out = web.render_timeline(mkboard())
    assert out.lstrip().startswith('<section id="timeline"')
    assert out.rstrip().endswith("</section>")


def test_timeline_segment_rects_have_class_and_title():
    segs = [("busy", NOW - 9000, NOW - 8000), ("sub", NOW - 7000, NOW - 6000),
            ("wait", NOW - 5000, NOW - 4000), ("wait_now", NOW - 3000, NOW - 1000)]
    out = web.render_timeline(mkboard(rows=[row(segs=segs)]))
    dom = Dom(out)
    found = seg_rects(dom)
    assert sorted(next(iter(c)) for _, c in found) == ["seg-busy", "seg-sub", "seg-wait", "seg-wait-now"]
    for n, _ in found:
        assert has_title(dom, n), n
    # the rect/title text is not empty
    titles = [n for n in dom.nodes if n["tag"] == "title" and n["id"] - 1 in {r["id"] for r, _ in found}]
    assert len(titles) == 4 and all(t["text"].strip() for t in titles)


def test_timeline_segment_geometry_orders_and_scales_with_time():
    a0, a1 = NOW - 9000, NOW - 8000   # 1000s
    b0, b1 = NOW - 6000, NOW - 3000   # 3000s
    out = web.render_timeline(mkboard(rows=[row(segs=[("busy", a0, a1), ("busy", b0, b1)])]))
    r = [n for n, _ in seg_rects(Dom(out))]
    assert len(r) == 2
    x = [num(n["attrs"]["x"]) for n in r]
    w = [num(n["attrs"]["width"]) for n in r]
    assert x[0] < x[1]
    assert w[1] / w[0] == pytest.approx(3.0, rel=0.05)


def test_timeline_one_group_per_lane_and_markers_on_own_lane():
    r1 = row("a", segs=[("busy", NOW - 9000, NOW - 8000)], commits=[], merges=[])
    r2 = row("b", segs=[("sub", NOW - 7000, NOW - 6000)], commits=[NOW - 5000],
             merges=[["T05", NOW - 4000]])
    base = Dom(web.render_timeline(mkboard(rows=[r1, r2])))
    bare = Dom(web.render_timeline(mkboard(rows=[
        row("a", segs=r1["segments"]), row("b", segs=r2["segments"])])))

    def lane_group(dom, cls):
        n = [n for n, c in seg_rects(dom) if cls in c][0]
        g = [a for a in n["anc"] if a["tag"] == "g"]
        assert g, "segment rect must live in an SVG group"
        return g[-1]

    g1, g2 = lane_group(base, "seg-busy"), lane_group(base, "seg-sub")
    assert g1["id"] != g2["id"]

    def marks(dom, kw):
        return [n for n in dom.nodes if any(kw in c for c in dom.classes(n))
                and not (dom.classes(n) & {"seg-busy", "seg-sub", "seg-wait", "seg-wait-now"})]

    def inside(n, g):
        return any(a["id"] == g["id"] for a in n["anc"]) or n["id"] == g["id"]

    for kw, extra in (("commit", 1), ("merge", 1)):
        added = len(marks(base, kw)) - len(marks(bare, kw))
        assert added >= extra, kw
        in2 = [n for n in marks(base, kw) if inside(n, g2)]
        in1 = [n for n in marks(base, kw) if inside(n, g1)]
        assert len(in2) >= 1 and not in1, kw
    assert "T05" in g2["text"] or any("T05" in n["text"] for n in base.nodes if inside(n, g2))
    assert "T05" not in g1["text"]


def test_timeline_has_now_line():
    dom = Dom(web.render_timeline(mkboard(rows=[row(segs=[("busy", NOW - 900, NOW - 300)])])))
    cands = [n for n in dom.nodes if any("now" in c and not c.startswith("seg") for c in dom.classes(n))]
    assert cands


def test_timeline_legend_wording():
    out = web.render_timeline(mkboard(rows=[row(segs=[("busy", NOW - 900, NOW - 300)])]))
    assert "闲置等输入" in out
    assert "还在闲置等输入" in out


def test_timeline_lane_summary_shows_work_and_wait():
    out = web.render_timeline(mkboard(rows=[row(segs=[("busy", NOW - 3600, NOW - 300)],
                                                 busy_s=3300, wait_s=360)]))
    # ruling: compact English units (45s / 55m / 2h28m), never 分钟 / 小时
    assert "干活 55m" in out
    assert "闲置等输入 6m" in out
    assert "分钟" not in out and "小时" not in out


def test_timeline_more_sessions_note():
    seg = [("busy", NOW - 900, NOW - 300)]
    assert "另有 4 个会话" in web.render_timeline(mkboard(rows=[row(segs=seg)], more=4))
    assert "另有" not in web.render_timeline(mkboard(rows=[row(segs=seg)], more=0))


def test_timeline_empty_board_does_not_crash():
    out = web.render_timeline(mkboard())
    assert "<section" in out and not seg_rects(Dom(out))


# ----------------------------------------------------------------- columns

def col_dom(b):
    return Dom(web.render_columns(b))


def test_columns_wrapper_and_four_columns():
    out = web.render_columns(mkboard())
    assert out.lstrip().startswith('<section id="columns"')
    assert out.rstrip().endswith("</section>")
    pos = [out.find(t) for t in ("等你", "进行中", "今天结束", "最优先待办")]
    assert all(p >= 0 for p in pos) and pos == sorted(pos)
    dom = Dom(out)
    colish = [n for n in dom.nodes if dom.classes(n) & {"col", "column", "board-col", "kanban-col"}]
    if colish:
        assert len(colish) == 4


def test_waiting_cards_class_order_and_content():
    w1 = sess("w1", "waiting", question="QUESTION-ONE?", idle_since=NOW - 7200, subagents_running=2,
              attach="zellij attach poly-one")
    w2 = sess("w2", "waiting", question="QUESTION-TWO?", idle_since=NOW - 600, attach="tmux attach -t two")
    out = web.render_columns(mkboard(waiting=[w1, w2]))
    dom = Dom(out)
    cards = [n for n in dom.nodes if "card-wait" in dom.classes(n)]
    assert len(cards) == 2
    assert out.index("QUESTION-ONE?") < out.index("QUESTION-TWO?")
    c1, c2 = cards
    assert "QUESTION-ONE?" in c1["text"] and "QUESTION-TWO?" not in c1["text"]
    assert "已闲置" in c1["text"] and "已闲置" in c2["text"]
    codes1 = [n for n in dom.nodes if n["tag"] == "code" and any(a["id"] == c1["id"] for a in n["anc"])]
    assert any("zellij attach poly-one" in c["text"] for c in codes1)
    codes2 = [n for n in dom.nodes if n["tag"] == "code" and any(a["id"] == c2["id"] for a in n["anc"])]
    assert any("tmux attach -t two" in c["text"] for c in codes2)
    assert "子代理 2 个在跑" in c1["text"]
    assert "子代理" not in c2["text"]


def test_waiting_order_follows_board_list_not_time():
    # board already sorted; the renderer must keep the given order even if idle_since disagrees
    a = sess("a", "waiting", question="FIRST-Q", idle_since=NOW - 10)
    b = sess("b", "waiting", question="SECOND-Q", idle_since=NOW - 9999)
    out = web.render_columns(mkboard(waiting=[a, b]))
    assert out.index("FIRST-Q") < out.index("SECOND-Q")


def test_project_display_name_strips_foreign_home_prefix():
    for pre in ("/home/someone", "/Users/someone"):
        w = sess("w", "waiting", question="q?", idle_since=NOW - 60, project=pre + "/dev/deep/proj-x")
        out = web.render_columns(mkboard(waiting=[w]))
        assert "dev/deep/proj-x" in out
        assert pre not in out


def test_idle_duration_compact_units():
    w = sess("w", "waiting", question="q?", idle_since=NOW - 3600 * 2, attach="a")
    out = web.render_columns(mkboard(waiting=[w]))
    assert "分钟" not in out and "小时" not in out


def test_waiting_empty_attach_shows_placeholder():
    w = sess("w", "waiting", question="q?", idle_since=NOW - 60, attach="")
    assert "（不在 zellij / tmux 里）" in web.render_columns(mkboard(waiting=[w]))


def test_waiting_remote_attach_shown_verbatim():
    w = sess("w", "waiting", question="q?", idle_since=NOW - 60, attach="ssh -t em-host zellij attach x")
    assert "ssh -t em-host zellij attach x" in web.render_columns(mkboard(waiting=[w]))


HOME_AT_IMPORT = os.path.expanduser("~")


@pytest.mark.parametrize("base", ["call", "import"])
def test_project_display_name_is_home_relative(base):
    # "relative to home": the home in effect when rendering (HOME is reset per test by the
    # autouse fixture) vs the home at import time; the spec does not say which one is meant.
    root = home() if base == "call" else HOME_AT_IMPORT
    w = sess("w", "waiting", question="q?", idle_since=NOW - 60, project=root + "/dev/deep/proj-x")
    out = web.render_columns(mkboard(waiting=[w]))
    assert "dev/deep/proj-x" in out
    assert root + "/dev/deep" not in out


def test_busy_card_task_bar_rows_and_progress():
    st = ["done"] * 6 + ["doing", "todo"]
    s = sess("b1", "busy", tasks=tasks(st, current="T07"))
    out = web.render_columns(mkboard(busy=[s]))
    dom = Dom(out)
    rects = [n for n in dom.nodes if n["tag"] == "rect"]

    def kind(n, k):
        return any(c == k or c.endswith("-" + k) for c in dom.classes(n))
    counts = {k: sum(1 for n in rects if kind(n, k)) for k in ("done", "doing", "todo", "stuck")}
    assert counts == {"done": 6, "doing": 1, "todo": 1, "stuck": 0}
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", out))
    assert "6 / 8 · 当前 T07" in re.sub(r"\s*([/·])\s*", r" \1 ", text)
    for mark, tid in (("✓", "T01"), ("◉", "T07"), ("○", "T08")):
        assert re.search(re.escape(mark) + r"\s*(<[^>]+>\s*)*" + tid, out), (mark, tid)
    assert "title of task 7" in out


def test_busy_card_stuck_task_distinct_rect():
    s = sess("b1", "busy", tasks=tasks(["done", "stuck", "todo"], current="T02"))
    dom = col_dom(mkboard(busy=[s]))
    rects = [n for n in dom.nodes if n["tag"] == "rect"]
    stuck = [n for n in rects if any(c == "stuck" or c.endswith("-stuck") for c in dom.classes(n))]
    assert len(stuck) == 1 and len(rects) >= 3


def test_many_tasks_fold_done_into_details():
    st = ["done"] * 8 + ["doing", "todo", "todo", "todo", "todo"]  # 13 tasks
    s = sess("b1", "busy", tasks=tasks(st, current="T09"))
    out = web.render_columns(mkboard(busy=[s]))
    details = re.findall(r"<details.*?</details>", out, re.S)
    assert details
    folded = " ".join(details)
    for i in range(1, 9):
        assert "title of task %d<" % i in folded.replace("title of task %d" % i, "title of task %d<" % i) \
            and ("title of task %d" % i) in folded
    for i in range(9, 14):
        assert ("title of task %d" % i) not in folded
        assert ("title of task %d" % i) in out
    assert "已完成 8" in out or "已完成8" in out


def test_few_tasks_do_not_fold_done():
    st = ["done"] * 4 + ["doing", "todo"]
    s = sess("b1", "busy", tasks=tasks(st, current="T05"))
    out = web.render_columns(mkboard(busy=[s]))
    for d in re.findall(r"<details.*?</details>", out, re.S):
        assert "title of task 1" not in d


def test_exactly_twelve_tasks_not_folded():
    s = sess("b1", "busy", tasks=tasks(["done"] * 6 + ["doing"] + ["todo"] * 5, current="T07"))
    out = web.render_columns(mkboard(busy=[s]))
    for d in re.findall(r"<details.*?</details>", out, re.S):
        assert "title of task 1<" not in d and "title of task 1 " not in d


def test_non_owner_session_names_owner():
    owner = sess("own", "busy", name="owner-session", tasks=tasks(["doing"], current="T01"))
    other = sess("oth", "busy", name="other-session", tasks=None, tasks_owner="own")
    out = web.render_columns(mkboard(busy=[owner, other]))
    assert "同一检出的计划由 owner-session 负责" in out
    assert out.count("同一检出的计划由") == 1


def test_errors_row_with_warning_sign():
    s = sess("e", "busy", errors=["bad json in x", "second problem"])
    out = web.render_columns(mkboard(busy=[s]))
    assert "⚠" in out and "bad json in x" in out
    clean = web.render_columns(mkboard(busy=[sess("e", "busy")]))
    assert "⚠" not in clean


def test_errors_capped_at_two():
    s = sess("e", "busy", errors=["err-one", "err-two", "err-three"])
    out = web.render_columns(mkboard(busy=[s]))
    assert "err-one" in out and "err-two" in out and "err-three" not in out


def test_idle_sessions_last_in_busy_column():
    b1 = sess("b1", "busy", name="busy-one")
    b2 = sess("b2", "sub", name="busy-two")
    i1 = sess("i1", "idle", name="idle-one", idle_since=NOW - 3000)
    out = web.render_columns(mkboard(busy=[b1, b2], idle=[i1]))
    assert out.index("busy-one") < out.index("idle-one")
    assert out.index("busy-two") < out.index("idle-one")
    assert "◌ 空闲" in out
    assert "◌ 空闲" not in web.render_columns(mkboard(busy=[b1]))


def test_ended_today_column():
    done = sess("d", "ended", end=NOW - 1800, duration=5400, wait_total=1200, commits_today=3,
                name="ended-one")
    out = web.render_columns(mkboard(ended=[done]))
    assert "ended-one" in out and "提交 3 次" in out


def test_ended_wait_total_none_renders_dash_not_zero():
    base = dict(end=NOW - 1800, duration=5400, commits_today=1)
    unknown = web.render_columns(mkboard(ended=[sess("d", "ended", wait_total=None, **base)]))
    known = web.render_columns(mkboard(ended=[sess("d", "ended", wait_total=600, **base)]))
    assert "—" in unknown
    assert "—" not in known


def test_top_todos_column_uses_home_relative_project_and_title():
    top = [{"host": "s-host", "project": HOME_AT_IMPORT + "/dev/projdeck", "title": "FIX-THE-THING", "flag": True},
           {"host": "s-host", "project": HOME_AT_IMPORT + "/quant", "title": "SECOND-THING", "flag": False}]
    out = web.render_columns(mkboard(top=top))
    assert "FIX-THE-THING" in out and "dev/projdeck" in out
    assert out.index("FIX-THE-THING") < out.index("SECOND-THING")


def test_columns_empty_board_does_not_crash():
    out = web.render_columns(mkboard())
    assert "card-wait" not in out


# ---------------------------------------------------------------- security

def evil_board():
    t = tasks(["done", "doing", "stuck", "todo"], current=EVIL)
    for it in t["items"]:
        it["title"] = EVIL
        it["note"] = EVIL2
        it["id"] = EVIL2
    t["label"], t["path"], t["current"] = EVIL, EVIL2, EVIL2
    kw = dict(name=EVIL + EVIL2, title=EVIL, last_reply=EVIL, question=EVIL2 + EVIL,
              last_human={"ts": NOW - 5, "text": EVIL}, attach="javascript:alert(1)\"onerror=1>",
              errors=[EVIL, EVIL2], project=home() + "/" + EVIL, host=EVIL2)
    w = sess("w", "waiting", idle_since=NOW - 100, subagents_running=1, **kw)
    b = sess("b", "busy", tasks=t, **kw)
    o = sess("o", "busy", tasks=None, tasks_owner="b", **kw)
    i = sess("i", "idle", idle_since=NOW - 100, **kw)
    e = sess("e", "ended", end=NOW - 50, duration=10, wait_total=1, commits_today=1, **kw)
    r = row("b", segs=[("busy", NOW - 900, NOW - 300)], commits=[NOW - 400], merges=[[EVIL2, NOW - 350]],
            project=home() + "/" + EVIL)
    top = [{"host": EVIL2, "project": home() + "/" + EVIL, "title": EVIL, "flag": True}]
    return mkboard(waiting=[w], busy=[b, o], idle=[i], ended=[e], rows=[r], top=top)


@pytest.mark.parametrize("fn", ["render_timeline", "render_columns"])
def test_untrusted_text_is_escaped(fn):
    out = getattr(web, fn)(evil_board())
    low = out.lower()
    assert "<script" not in low and "<img" not in low
    assert 'style="' not in low and "style='" not in low
    dom = Dom(out)
    for n in dom.nodes:
        assert n["tag"] not in ("script", "img", "iframe", "object", "embed", "link"), n["tag"]
        for k, v in n["attrs"].items():
            assert not k.startswith("on"), (n["tag"], k)
            assert k != "style"
            assert not (v or "").strip().lower().startswith("javascript:"), (k, v)
    assert not re.search(r"https?://", re.sub(r'xmlns(:\w+)?="[^"]*"', "", out))


def test_untrusted_text_still_visible_as_escaped_text():
    out = web.render_columns(evil_board())
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_attach_code_escaped_not_attribute_injected():
    w = sess("w", "waiting", question="q", idle_since=NOW - 60, attach='x"><b onclick=1>y')
    out = web.render_columns(mkboard(waiting=[w]))
    assert "<b onclick" not in out
    assert all(not k.startswith("on") for n in Dom(out).nodes for k in n["attrs"])


def test_timeline_project_name_with_script_is_escaped():
    r = row(segs=[("busy", NOW - 900, NOW - 300)], project=home() + "/" + EVIL)
    out = web.render_timeline(mkboard(rows=[r], more=1))
    assert "<script" not in out.lower() and "<img" not in out.lower()


# -------------------------------------------------------- with board.build

def test_renders_real_board_build_output():
    cfg = {"hosts": [{"name": "s-host", "local": True}]}
    live = {"host": "s-host", "collected_at": NOW - 5, "projects": {}, "sessions": [
        sess("w", "waiting", question="REAL-Q?", idle_since=NOW - 1800,
             segments=[["busy", NOW - 3000, NOW - 1800], ["wait_now", NOW - 1800, NOW]]),
        sess("b", "busy", started_at=NOW - 4000, segments=[["busy", NOW - 4000, NOW - 100]]),
    ]}
    cache = {"host": "s-host", "collected_at": NOW - 5, "projects": []}
    b = board_mod.build([cache], [live], cfg, NOW)
    cols = web.render_columns(b)
    assert "REAL-Q?" in cols and "card-wait" in cols
    tl = Dom(web.render_timeline(b))
    assert len(seg_rects(tl)) >= 3


# ------------------------------------------------ round 3: runtime, data-key

def test_busy_card_shows_running_duration():
    s = sess("r", "busy", started_at=NOW - 5400)
    out = web.render_columns(mkboard(busy=[s]))
    assert "运行中 1h30m" in out


def test_busy_card_without_started_at_shows_plain_running():
    s = sess("r", "busy", started_at=None)
    out = web.render_columns(mkboard(busy=[s]))
    assert "运行中" in out
    assert not re.search(r"运行中\s*\d", out)


def _many_tasks_board(sid, host):
    st = ["done"] * 8 + ["doing", "todo", "todo", "todo", "todo"]
    return mkboard(busy=[sess(sid, "busy", host=host, tasks=tasks(st, current="T09"))])


def test_fold_details_has_data_key_with_host_and_id():
    out = web.render_columns(_many_tasks_board("sid9", "s-host"))
    dom = Dom(out)
    keys = [n["attrs"].get("data-key") for n in dom.nodes if n["tag"] == "details"]
    assert "tasks-s-host-sid9" in keys


def test_fold_details_data_key_is_escaped():
    out = web.render_columns(_many_tasks_board('s"onerror=1>', '<script>h"'))
    dom = Dom(out)
    assert "<script" not in out.lower()
    keys = [n["attrs"].get("data-key") for n in dom.nodes if n["tag"] == "details"]
    assert 'tasks-<script>h"-s"onerror=1>' in keys  # round-trips only if properly escaped
    assert all(not k.startswith("on") for n in dom.nodes for k in n["attrs"])


def test_no_fold_no_data_key_details_for_few_tasks():
    s = sess("f", "busy", tasks=tasks(["done"] * 3 + ["doing"], current="T04"))
    dom = Dom(web.render_columns(mkboard(busy=[s])))
    assert not [n for n in dom.nodes if n["tag"] == "details" and n["attrs"].get("data-key")]
