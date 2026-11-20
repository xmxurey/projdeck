import re

from projdeck import web

NOW = 1_800_000_000
EVIL = "<script>alert(1)</script><img src=x onerror=1>"
ESC = "&lt;script&gt;alert(1)&lt;/script&gt;"


def item(title, done=False, section=None, text=None):
    return {"title": title, "done": done, "section": section, "text": text or title}


def entry(project, items, host="s-host", updated=NOW - 100, raw=None):
    return {"host": host, "project": project, "open": sum(1 for i in items if not i["done"]),
            "done": sum(1 for i in items if i["done"]), "updated": updated, "items": items, "raw": raw}


def board(**kw):
    b = {"hosts": [], "summary": {"waiting": 1, "busy": 2, "idle": 1, "open_todos": 9,
                                  "todo_projects": 2, "zellij_exited": [], "zellij_exited_n": 0,
                                  "no_backlog": []},
         "waiting": [], "busy": [], "idle": [], "ended_today": [], "stale": [],
         "stale_host_sessions": [], "top_todos": [], "backlogs": [],
         "lanes": {"start": NOW - 4 * 3600, "end": NOW, "rows": [], "more": 0}}
    b.update(kw)
    return b


def test_page_order_scripts_and_no_style():
    html = web.render(board(backlogs=[entry("/home/u/dev/p", [item("a")])]), "home")
    assert html.index('id="timeline"') < html.index('id="columns"') < html.index('id="backlog"')
    assert re.findall(r"<script\b[^>]*>", html) == ['<script src="/static/poll.js">']
    assert 'style="' not in html
    assert 'data-poll="/fragment/home"' in html and 'data-interval="5000"' in html
    assert "/static/base.css" in html and "/static/app.css" in html
    assert "http://" not in html and "https://" not in html


def test_header_counters_and_idle_counted_apart():
    html = web.render(board(), "home")
    assert "<b>1</b>等你回复" in html
    assert "<b>2</b>进行中" in html and "◌ 空闲 1" in html
    assert "<b>9</b>条待办 · 2 个项目" in html


def test_header_host_freshness():
    hosts = [{"name": "s-host", "live_at": NOW - 2, "live_synced_at": NOW - 2, "collect_at": NOW - 60, "stale_since": None, "error": None},
             {"name": "em-host", "live_at": NOW - 120, "live_synced_at": NOW - 120, "collect_at": None, "stale_since": None, "error": None},
             {"name": "mac", "live_at": NOW - 900, "live_synced_at": NOW - 900, "collect_at": None, "stale_since": NOW - 800,
              "error": EVIL}]
    html = web.render(board(hosts=hosts), "home")
    assert "s-host 实时" in html and "em-host 2m 前采集" in html
    assert "mac 15 分钟前的数据：" + ESC in html
    assert "<script>alert" not in html


def test_fragment_is_main_inner_html():
    b = board(backlogs=[entry("/home/u/p", [item("a")])])
    full, frag = web.render(b, "home"), web.render(b, "home", fragment=True)
    inner = re.search(r"<main[^>]*>(.*)</main>", full, re.S).group(1)
    assert frag == inner
    assert "<html" not in frag and "<head" not in frag


def test_checkboxes_all_disabled_and_note_lines():
    items = [item("t%d" % i) for i in range(8)] + [item("d", True)]
    html = web.render_backlog(board(backlogs=[entry("/home/u/p", items)]))
    boxes = re.findall(r"<input[^>]*>", html)
    assert boxes and all("disabled" in b for b in boxes)
    assert "勾选框第二期启用" in html
    assert "没有待办文件：" in html


def test_first_five_open_then_fold_grouped_by_section_and_done_folded():
    items = [item("t%d" % i, section="S1" if i < 7 else "S2") for i in range(8)] + [item("dd", True)]
    html = web.render_backlog(board(backlogs=[entry("/home/u/p", items)]))
    head, rest = html.split("<details", 1)
    for i in range(5):
        assert ">t%d<" % i in head
    assert ">t5<" not in head
    assert "展开全部 8 条" in html
    more = re.search(r"<details[^>]*>(?:(?!</details>).)*展开全部.*?</details>", html, re.S).group(0)
    assert ">t5<" in more and ">t7<" in more and ">S1<" in more and ">S2<" in more
    done = re.search(r"<details[^>]*>(?:(?!</details>).)*已完成 1 条.*?</details>", html, re.S).group(0)
    assert ">dd<" in done and "open" not in re.match(r"<details[^>]*>", done).group(0)
    assert re.search(r"<input[^>]*checked[^>]*disabled", done)


def test_few_items_have_no_expand_fold():
    html = web.render_backlog(board(backlogs=[entry("/home/u/p", [item("a"), item("b")])]))
    assert "展开全部" not in html


def test_every_details_has_key_and_collapse_narrow_and_keys_distinct():
    bl = [entry("/home/u/p", [item("t%d" % i) for i in range(7)] + [item("d", True)]),
          entry("/home/u/p", [item("t%d" % i) for i in range(7)] + [item("d", True)], host="em-host")]
    html = web.render_backlog(board(backlogs=bl))
    tags = re.findall(r"<details[^>]*>", html)
    assert len(tags) == 4
    assert all("data-key=" in t and "data-collapse-narrow" in t for t in tags)
    assert len({re.search(r'data-key="([^"]*)"', t).group(1) for t in tags}) == 4


def test_unparseable_backlog_shows_escaped_raw_in_pre():
    html = web.render_backlog(board(backlogs=[entry("/home/u/p", [], raw="# x\n" + EVIL)]))
    assert re.search(r"<pre[^>]*>[^<]*" + re.escape(ESC), html)
    assert "<script" not in html and "<img" not in html


def test_injection_in_title_text_section_project_and_missing_list():
    bl = entry("/home/u/" + EVIL, [item(EVIL, section=EVIL, text=EVIL)] + [item("x%d" % i) for i in range(6)])
    b = board(backlogs=[bl])
    b["summary"]["no_backlog"] = [{"host": "s-host", "project": "/home/u/" + EVIL}]
    html = web.render(b, "home")
    assert "<script" not in html.replace('<script src="/static/poll.js">', "")
    assert "<img" not in html
    assert ESC in html


def test_old_backlog_marked_and_today_label():
    html = web.render_backlog(board(backlogs=[
        entry("/home/u/old", [item("a")], updated=NOW - 40 * 86400),
        entry("/home/u/new", [item("a")], updated=NOW - 10)]))
    assert re.search(r'class="bl-updated old"', html)
    assert "今天更新" in html


def test_no_backlog_entries_use_project_names_and_host_when_ambiguous():
    b = board()
    b["summary"]["no_backlog"] = [{"host": "s-host", "project": "/home/someuser/dev/projdeck"},
                                  {"host": "s-host", "project": "/home/someuser/a/same"},
                                  {"host": "em-host", "project": "/home/em-host/a/same"}]
    html = web.render_backlog(b)
    assert "没有待办文件：dev/projdeck" in html
    assert "s-host · a/same" in html and "em-host · a/same" in html


def test_zellij_exited_details_folded_with_escaped_commands():
    b = board()
    b["summary"]["zellij_exited"] = [
        {"host": "s-host", "name": "a", "cmd": "zellij delete-session a"},
        {"host": "s-host", "name": EVIL, "cmd": "zellij delete-session '%s'" % EVIL}]
    b["summary"]["zellij_exited_n"] = 2
    html = web.render(b, "home")
    m = re.search(r"<details[^>]*zellij-exited[^>]*>.*?</details>", html, re.S)
    assert m
    d = m.group(0)
    assert "open" not in re.match(r"<details[^>]*>", d).group(0).split()
    assert "已退出的 zellij 会话 2 个" in d
    assert len(re.findall(r"<code>", d)) == 2
    assert "<code>zellij delete-session a</code>" in d and ESC in d
    assert "proj 不会自动删除你自己起名的会话" in d
    assert "<script>alert" not in html


def test_zellij_exited_absent_when_zero():
    assert "已退出的 zellij" not in web.render(board(), "home")


def test_stale_host_sessions_greyed_in_their_column_with_age():
    s = {"id": "r", "host": "em-host", "project": "/home/u/p", "name": "r1", "state": "waiting",
         "idle_since": NOW - 5000, "question": "q?", "last_reply": "", "last_human": {}, "age": 720,
         "attach": "", "errors": [], "subagents_running": 0}
    html = web.render_columns(board(stale_host_sessions=[s]))
    col = re.search(r'<div class="column col-wait">.*?</div>\s*<div class="column col-busy">', html, re.S).group(0)
    assert "card-stale-host" in col and "12 分钟前的数据" in col
    assert '<h3>等你 <span class="count">0</span>' in col


def test_stale_fold_line_counts_background_sessions():
    st = [{"project": "/home/u/p", "host": "s-host", "name": "old1"},
          {"project": "/home/u/p", "host": "s-host", "name": "old2"}]
    html = web.render(board(stale=st), "home")
    assert "另有 2 个超过 1 天没有动静的后台会话" in html
    assert "另有" not in web.render(board(), "home").replace("另有 0", "")


def test_app_css_has_no_urls_and_only_spec_colours():
    css = open(web.__file__.replace("web.py", "web/app.css")).read()
    assert "http://" not in css and "https://" not in css and "url(" not in css
    allowed = {"#2F5D8A", "#8DB4DC", "#E8954A", "#F6CFA6", "#2F7D5B", "#F5F6F8"}
    assert set(re.findall(r"#[0-9A-Fa-f]{3,8}\b", css)) <= allowed


def test_unknown_page_rejected():
    import pytest
    with pytest.raises(ValueError):
        web.render(board(), "nope")
