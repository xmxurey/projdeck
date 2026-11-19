"""Independent spec tests for T21: web.render_backlog and web.render(board, "home")."""
import re
from html.parser import HTMLParser

from projdeck import web

NOW = 1_800_000_000
EVIL = '<script>alert(1)</script><img src=x onerror=1>'
EVIL2 = '"onerror=1>javascript:alert(1)'
P1 = "/home/someone/dev/deep/proj-x"
P2 = "/home/someone/dev/other"


def item(title, done=False, section=None, text=None):
    return {"title": title, "done": done, "section": section, "text": text or title}


def bl(project, items, raw=None, host="s-host", updated=NOW - 3600):
    return {"host": host, "project": project, "open": sum(1 for i in items if not i["done"]),
            "done": sum(1 for i in items if i["done"]), "updated": updated, "items": items, "raw": raw}


def sess(sid, state):
    return {"id": sid, "project": P1, "host": "s-host", "name": "n-" + sid, "title": "t-" + sid,
            "state": state, "status": "busy", "started_at": NOW - 7200, "idle_since": None,
            "question": "", "last_reply": "", "last_human": None, "last_input": "human",
            "subagents_running": 0, "segments": [], "attach": "zellij attach z", "tasks": None,
            "tasks_owner": None, "errors": []}


def board(backlogs=(), no_backlog=(), exited=(), hosts=None, waiting=0, busy=0, idle=0,
          open_todos=0, todo_projects=0):
    hosts = hosts if hosts is not None else [
        {"name": "s-host", "live_at": NOW - 30, "collect_at": NOW - 60, "stale_since": None,
         "error": None, "source": "local"}]
    return {"hosts": hosts,
            "summary": {"waiting": waiting, "busy": busy, "sub": 0, "idle": idle,
                        "open_todos": open_todos, "todo_projects": todo_projects,
                        "today": {"busy": 0, "sub": 0, "wait": 0},
                        "zellij_exited": list(exited), "zellij_exited_n": len(exited),
                        "no_backlog": list(no_backlog)},
            "waiting": [sess("w%d" % i, "waiting") for i in range(waiting)],
            "busy": [sess("b%d" % i, "busy") for i in range(busy)],
            "idle": [sess("i%d" % i, "idle") for i in range(idle)],
            "ended_today": [], "stale": [], "stale_host_sessions": [],
            "lanes": {"start": NOW - 3 * 3600, "end": NOW, "rows": [], "more": 0},
            "top_todos": [], "backlogs": list(backlogs), "tiles": []}


class Dom(HTMLParser):
    def __init__(self, s):
        super().__init__(convert_charrefs=True)
        self.nodes, self.stack = [], []
        self.feed(s)

    def handle_starttag(self, tag, attrs):
        n = {"tag": tag, "attrs": dict(attrs), "anc": list(self.stack), "text": ""}
        self.nodes.append(n)
        if tag not in ("rect", "line", "path", "circle", "polygon", "br", "img", "input",
                       "use", "ellipse", "meta", "link"):
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.nodes.append({"tag": tag, "attrs": dict(attrs), "anc": list(self.stack), "text": ""})

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, d):
        for n in self.stack:
            n["text"] += d

    def find(self, tag):
        return [n for n in self.nodes if n["tag"] == tag]


def section(b):
    return web.render_backlog(b)


def inner_main(html):
    m = re.search(r"<main\b[^>]*>(.*)</main>", html, re.S)
    assert m, "no <main>"
    return m.group(1)


def sec_html(html):
    m = re.search(r'<section id="backlog".*?</section>', html, re.S)
    assert m
    return m.group(0)


def many(n, done_n=0, prefix="todo"):
    return [item("%s-%d" % (prefix, i)) for i in range(n)] + \
           [item("fin-%d" % i, done=True) for i in range(done_n)]


# ---------- acceptance 1: page assembly ----------

def test_home_ids_in_order():
    html = web.render(board([bl(P1, many(2))], open_todos=2, todo_projects=1), "home")
    pos = [html.find(x) for x in ('id="timeline"', 'id="columns"', 'id="backlog"')]
    assert all(p >= 0 for p in pos) and pos == sorted(pos)


def test_home_only_poll_script_and_no_style_attr():
    html = web.render(board([bl(P1, [item(EVIL)], raw=None)], waiting=1, busy=1), "home")
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts == ['<script src="/static/poll.js">']
    assert 'style="' not in html


def test_home_layout_refs_and_poll_attrs():
    html = web.render(board(), "home")
    assert "/static/base.css" in html and "/static/app.css" in html
    assert html.lstrip().lower().startswith("<!doctype html")
    mains = Dom(html).find("main")
    assert len(mains) == 1
    assert mains[0]["attrs"].get("data-poll") == "/fragment/home"
    assert mains[0]["attrs"].get("data-interval") == "5000"
    assert not re.search(r"https?://", html)


def test_home_header_three_counts_and_host_freshness():
    b = board([bl(P1, many(3))], waiting=3, busy=2, idle=1, open_todos=17, todo_projects=4,
              hosts=[{"name": "s-host", "live_at": NOW - 30, "collect_at": NOW - 60,
                      "stale_since": None, "error": None, "source": "local"},
                     {"name": "em-host", "live_at": NOW - 120, "collect_at": NOW - 300,
                      "stale_since": None, "error": None, "source": "ssh"}])
    html = web.render(b, "home")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    assert "等你回复" in text and "进行中" in text
    assert re.search(r"17\s*条待办\s*·\s*4\s*个项目", text)
    assert "s-host" in text and "em-host" in text


def test_home_header_host_error_escaped():
    b = board(hosts=[{"name": "s-host", "live_at": None, "collect_at": None, "stale_since": None,
                      "error": EVIL, "source": "ssh"}])
    html = web.render(b, "home")
    assert "<script>alert" not in html and "<img" not in html
    assert "&lt;script&gt;" in html


def test_home_empty_board_still_has_all_regions():
    html = web.render(board(), "home")
    for x in ('id="timeline"', 'id="columns"', 'id="backlog"'):
        assert x in html


# ---------- acceptance 4: fragment ----------

def test_fragment_is_main_inner_of_full_page():
    b = board([bl(P1, many(7, 2), raw=None)], no_backlog=[{"host": "s-host", "project": P2}],
              waiting=1, open_todos=7, todo_projects=1)
    full = web.render(b, "home")
    frag = web.render(b, "home", fragment=True)
    assert re.sub(r"\s+", "", inner_main(full)) == re.sub(r"\s+", "", frag)
    low = frag.lower()
    assert "<html" not in low and "<head" not in low and "<body" not in low
    assert 'id="backlog"' in frag and 'id="timeline"' in frag
    assert "<script" not in low


def test_fragment_default_is_false():
    b = board()
    assert web.render(b, "home") == web.render(b, "home", fragment=False)
    assert "<html" in web.render(b, "home").lower()


# ---------- acceptance 2: backlog section ----------

def test_backlog_section_wrapper():
    s = section(board([bl(P1, many(1))]))
    assert s.lstrip().startswith("<section") and 'id="backlog"' in s.split(">")[0]
    assert s.rstrip().endswith("</section>")


def test_every_checkbox_disabled():
    s = section(board([bl(P1, many(9, 3)), bl(P2, many(2, 1))]))
    boxes = [n for n in Dom(s).find("input") if n["attrs"].get("type") == "checkbox"]
    assert len(boxes) >= 5
    assert all("disabled" in n["attrs"] for n in boxes)


def test_first_five_open_visible_rest_in_expand_details():
    items = many(8)
    s = section(board([bl(P1, items)]))
    dom = Dom(s)
    dets = dom.find("details")
    expand = [d for d in dets if "展开全部 8 条" in re.sub(r"\s+", " ", d["text"])]
    assert len(expand) == 1
    inside = expand[0]["text"]
    outside_titles = [t for t in ("todo-%d" % i for i in range(8))
                      if t in s and t not in inside]
    assert outside_titles == ["todo-%d" % i for i in range(5)]
    for i in range(5, 8):
        assert "todo-%d" % i in inside


def test_five_or_fewer_open_has_no_expand_details():
    s = section(board([bl(P1, many(5))]))
    assert "展开全部" not in s
    for i in range(5):
        assert "todo-%d" % i in s


def test_expand_details_groups_by_section():
    items = [item("a%d" % i, section="Alpha") for i in range(5)] + \
            [item("b1", section="Beta-sec"), item("b2", section="Beta-sec"), item("c1", section="Gamma-sec")]
    s = section(board([bl(P1, items)]))
    dom = Dom(s)
    expand = [d for d in dom.find("details") if "展开全部" in d["text"]][0]["text"]
    assert "展开全部 8 条" in re.sub(r"\s+", " ", expand)
    assert "Beta-sec" in expand and "Gamma-sec" in expand
    assert expand.index("Beta-sec") < expand.index("b1") < expand.index("b2") < expand.index("Gamma-sec") < expand.index("c1")


def test_done_items_in_closed_details():
    s = section(board([bl(P1, many(2, 3))]))
    dets = Dom(s).find("details")
    done_d = [d for d in dets if "fin-0" in d["text"]]
    assert len(done_d) == 1
    assert "open" not in done_d[0]["attrs"]
    for i in range(3):
        assert "fin-%d" % i in done_d[0]["text"]
    # done items are not shown outside any details
    outside = re.sub(r"<details\b.*?</details>", "", s, flags=re.S)
    assert "fin-" not in outside


def test_open_items_not_in_done_details():
    s = section(board([bl(P1, many(2, 2))]))
    done_d = [d for d in Dom(s).find("details") if "fin-0" in d["text"]][0]
    assert "todo-0" not in done_d["text"]


def test_no_done_items_no_done_details():
    s = section(board([bl(P1, many(2))]))
    assert "fin-" not in s
    assert Dom(s).find("details") == []


def test_all_done_project_renders_without_crash():
    s = section(board([bl(P1, many(0, 3))]))
    assert "fin-0" in s


def test_phase2_checkbox_note_present():
    s = section(board([bl(P1, many(2))]))
    assert "勾选框第二期启用" in s


def test_no_backlog_file_line_lists_relative_project_names():
    s = section(board([bl(P1, many(1))], no_backlog=[{"host": "s-host", "project": P2}]))
    idx = s.index("没有待办文件：")
    tail = s[idx:]
    assert "dev/other" in re.sub(r"<[^>]+>", "", tail)
    assert "/home/someone" not in re.sub(r"<[^>]+>", "", tail)


def test_no_backlog_line_says_none_when_empty():
    s = section(board([bl(P1, many(1))], no_backlog=[]))
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s))
    assert re.search(r"没有待办文件：\s*无", text)


def test_no_backlog_same_project_two_hosts_both_listed():
    s = section(board(no_backlog=[{"host": "s-host", "project": P2}, {"host": "em-host", "project": P2}]))
    tail = re.sub(r"<[^>]+>", " ", s[s.index("没有待办文件："):])
    assert "s-host" in tail and "em-host" in tail


def test_empty_backlogs_renders_section():
    s = section(board())
    assert 'id="backlog"' in s and "没有待办文件：" in s


def test_project_name_relative_to_home_in_cards():
    s = section(board([bl(P1, many(1))]))
    text = re.sub(r"<[^>]+>", "", s)
    assert "dev/deep/proj-x" in text
    assert "/home/someone" not in text


def test_card_shows_open_and_done_counts():
    s = section(board([bl(P1, many(4, 3))]))
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))
    assert "4 条 · 已完成 3" in text  # F9: the counts in their place, not any 4 and 3


def test_one_card_per_project_items_do_not_mix():
    s = section(board([bl(P1, many(2, prefix="aaa")), bl(P2, many(2, prefix="bbb"))]))
    assert s.index("aaa-0") < s.index("aaa-1") < s.index("bbb-0")
    assert s.index("dev/deep/proj-x") < s.index("dev/other")


# ---------- acceptance 3: raw, injection, data-key ----------

def test_unparsable_raw_in_pre_escaped():
    raw = "free <b>text</b> & more\n" + EVIL
    s = section(board([bl(P1, [], raw=raw)]))
    pres = Dom(s).find("pre")
    assert len(pres) == 1
    assert "<b>text</b> & more" in pres[0]["text"]  # parsed back to the original string
    assert "&lt;b&gt;text&lt;/b&gt; &amp; more" in s
    assert "<b>" not in s and "<script>alert" not in s and "<img" not in s


def test_parsable_project_has_no_pre():
    s = section(board([bl(P1, many(2))]))
    assert Dom(s).find("pre") == []


def test_unparsable_project_has_no_checkbox():
    s = section(board([bl(P1, [], raw="plain")]))
    assert Dom(s).find("input") == []


def _assert_safe(html, allowed_scripts=0):
    dom = Dom(html)
    assert len(dom.find("script")) == allowed_scripts
    assert dom.find("img") == []
    assert dom.find("iframe") == []
    for n in dom.nodes:
        for k, v in n["attrs"].items():
            assert not k.startswith("on"), (n["tag"], k)
            if k in ("href", "src", "action", "formaction"):
                assert not (v or "").strip().lower().startswith("javascript:")
    assert 'style="' not in html


def test_injection_in_title_text_section_raw_project_escaped():
    items = [item(EVIL, section=EVIL2, text=EVIL + EVIL2),
             item(EVIL2, done=True, section=EVIL, text=EVIL2)] + many(6)
    b = board([bl("/home/x/" + EVIL2, items), bl(P2, [], raw=EVIL + EVIL2)],
              no_backlog=[{"host": EVIL, "project": "/home/x/" + EVIL}])
    s = section(b)
    assert "<script>alert" not in s
    assert "&lt;script&gt;" in s
    _assert_safe(s)
    _assert_safe(web.render(b, "home"), allowed_scripts=1)


def test_injection_full_page_and_fragment_no_executable_content():
    b = board([bl(P1, [item(EVIL, text=EVIL)])], exited=[
        {"host": "s-host", "name": EVIL, "cmd": "zellij delete-session " + EVIL}], hosts=[
        {"name": EVIL2, "live_at": NOW, "collect_at": NOW, "stale_since": None,
         "error": EVIL, "source": "ssh"}])
    _assert_safe(web.render(b, "home"), allowed_scripts=1)
    _assert_safe(web.render(b, "home", fragment=True), allowed_scripts=0)


def test_all_details_have_data_key_and_collapse_narrow():
    b = board([bl(P1, many(8, 2)), bl(P2, many(7, 1)), bl("/home/someone/dev/raw", [], raw="r")])
    s = section(b)
    dets = Dom(s).find("details")
    assert len(dets) >= 4
    for d in dets:
        assert d["attrs"].get("data-key"), d
        assert "data-collapse-narrow" in d["attrs"], d


def test_data_keys_unique_across_page_backlog():
    b = board([bl(P1, many(8, 2)), bl(P2, many(7, 1))])
    keys = [d["attrs"]["data-key"] for d in Dom(section(b)).find("details")]
    assert len(keys) == len(set(keys))


def test_data_keys_stable_between_renders_and_include_project():
    b = board([bl(P1, many(8, 2))])
    k1 = [d["attrs"]["data-key"] for d in Dom(section(b)).find("details")]
    k2 = [d["attrs"]["data-key"] for d in Dom(section(b)).find("details")]
    assert k1 == k2
    assert all("proj-x" in k or P1 in k for k in k1)


# ---------- acceptance 5: exited zellij sessions ----------

def exited(*names):
    return [{"host": "s-host", "name": n, "cmd": "zellij delete-session " + n} for n in names]


def test_exited_details_shown_collapsed_with_two_commands():
    b = board(exited=exited("alpha", "beta"))
    html = web.render(b, "home")
    dom = Dom(html)
    ds = [d for d in dom.find("details") if "已退出的 zellij 会话 2 个" in re.sub(r"\s+", " ", d["text"])]
    assert len(ds) == 1
    d = ds[0]
    assert "open" not in d["attrs"]
    codes = [n for n in dom.find("code") if d in n["anc"]]
    cmds = [c["text"] for c in codes]
    assert "zellij delete-session alpha" in cmds and "zellij delete-session beta" in cmds
    assert "proj 不会自动删除你自己起名的会话" in d["text"]


def test_exited_details_is_in_header_before_timeline():
    html = web.render(board(exited=exited("alpha", "beta")), "home")
    assert html.index("已退出的 zellij 会话 2 个") < html.index('id="timeline"')


def test_exited_zero_not_shown():
    html = web.render(board(exited=[]), "home")
    assert "已退出的 zellij" not in html
    assert "proj 不会自动删除" not in html


def test_exited_one_shows_count_one():
    html = web.render(board(exited=exited("solo")), "home")
    assert "已退出的 zellij 会话 1 个" in html
    assert "zellij delete-session solo" in html


def test_exited_commands_escaped():
    cmd = "zellij delete-session '<script>x</script>'"
    b = board(exited=[{"host": "s-host", "name": "<script>x</script>", "cmd": cmd},
                      {"host": "s-host", "name": "b", "cmd": "zellij delete-session b"}])
    html = web.render(b, "home")
    assert "<script>x" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "已退出的 zellij 会话 2 个" in html
    _assert_safe(html, allowed_scripts=1)


def test_exited_in_fragment_too():
    frag = web.render(board(exited=exited("a", "b")), "home", fragment=True)
    assert "已退出的 zellij 会话 2 个" in frag
