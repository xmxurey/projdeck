import re

from projdeck import web

NOW = 1_800_000_000
EVIL = "<script>alert(1)</script><img src=x onerror=1>"


def sess(sid, state="busy", **kw):
    d = {"id": sid, "host": "s-host", "project": "/home/someuser/dev/projdeck", "name": "n-" + sid,
         "title": "", "state": state, "idle_since": NOW - 600, "question": "", "last_reply": "",
         "last_human": {"ts": 0, "text": ""}, "last_input": "human", "subagents_running": 0,
         "segments": [], "attach": "zellij attach z-" + sid, "tasks": None, "tasks_owner": None,
         "errors": []}
    d.update(kw)
    return d


def board(**kw):
    b = {"hosts": [], "summary": {}, "waiting": [], "busy": [], "idle": [], "ended_today": [],
         "stale": [], "stale_host_sessions": [], "top_todos": [],
         "lanes": {"start": NOW - 4 * 3600, "end": NOW, "rows": [], "more": 0}}
    b.update(kw)
    return b


def items(n, done):
    return [{"id": "T%02d" % i, "title": "task %d" % i, "note": "",
             "status": "done" if i <= done else ("doing" if i == done + 1 else "todo")}
            for i in range(1, n + 1)]


def cards(html, cls):
    return re.findall(r'<article class="card %s.*?</article>' % cls, html, re.S)


# ----- timeline

def lane(**kw):
    r = {"host": "s-host", "project": "/home/someuser/dev/projdeck", "session_id": "a", "state": "busy",
         "segments": [["busy", NOW - 3600, NOW - 1800], ["sub", NOW - 1800, NOW - 1200],
                      ["wait", NOW - 1200, NOW - 600], ["wait_now", NOW - 600, NOW]],
         "commits": [NOW - 900], "merges": [["T07", NOW - 300]], "busy_s": 1800, "wait_s": 1200}
    r.update(kw)
    return r


def test_timeline_segments_marks_now_legend():
    b = board(busy=[sess("a")], lanes={"start": NOW - 4 * 3600, "end": NOW, "rows": [lane()], "more": 0})
    html = web.render_timeline(b)
    assert html.startswith('<section id="timeline">') and html.rstrip().endswith("</section>")
    for k in ("seg-busy", "seg-sub", "seg-wait", "seg-wait-now"):
        assert re.search(r'<rect class="seg %s [^>]*><title>' % k, html)
    assert "mark-commit" in html and "mark-merge" in html and ">T07<" in html
    assert "now-line" in html
    assert "闲置等输入" in html and "还在闲置等输入" in html
    assert "另有" not in html
    assert "dev/projdeck" in html and "/home/someuser" not in html


def test_timeline_more():
    b = board(lanes={"start": NOW - 4 * 3600, "end": NOW, "rows": [], "more": 3})
    assert "另有 3 个会话" in web.render_timeline(b)


def test_timeline_wait_unknown_is_dash():
    b = board(lanes={"start": NOW - 4 * 3600, "end": NOW, "rows": [lane(wait_s=None)], "more": 0})
    assert "闲置等输入 —" in web.render_timeline(b)


# ----- columns

def test_four_columns_and_wait_order_and_content():
    w1, w2 = sess("w1", "waiting", question="Q-first"), sess("w2", "waiting", question="Q-second",
                                                              subagents_running=2)
    html = web.render_columns(board(waiting=[w1, w2]))
    assert html.count('class="column ') == 4
    wc = cards(html, "card-wait")
    assert len(wc) == 2 and wc[0].index("Q-first") >= 0 and "Q-second" in wc[1]
    assert "已闲置 10m" in wc[0] and "<code>zellij attach z-w1</code>" in wc[0]
    assert "子代理" not in wc[0] and "子代理 2 个在跑" in wc[1]


def test_no_attach_text():
    html = web.render_columns(board(waiting=[sess("w", "waiting", attach="")]))
    assert "（不在 zellij / tmux 里）" in html


def test_busy_tasks_progress_and_fold():
    tasks = {"label": "台账", "path": "p", "current": "T07", "items": items(8, 6), "merges": []}
    html = web.render_columns(board(busy=[sess("a", tasks=tasks)]))
    bc = cards(html, "card-busy")[0]
    assert "6 / 8 · 当前 T07" in bc
    assert len(re.findall(r'<rect class="task-', bc)) == 8
    for k in ("task-done", "task-doing", "task-todo"):
        assert k in bc
    assert "✓" in bc and "◉" in bc and "○" in bc
    assert "<details>" not in bc
    big = {"label": "x", "current": "T14", "items": items(14, 13), "merges": []}
    bc = cards(web.render_columns(board(busy=[sess("a", tasks=big)])), "card-busy")[0]
    assert "<summary>已完成 13 个</summary>" in bc


def test_non_owner_and_errors_and_peer():
    owner = sess("o", name="lead", tasks={"items": items(2, 1), "current": "T02"})
    other = sess("p", tasks_owner="o", errors=["e1", "e2", "e3"], last_input="peer",
                 last_human={"ts": 1, "text": "hello"})
    html = web.render_columns(board(busy=[owner, other]))
    pc = cards(html, "card-busy")[1]
    assert "同一检出的计划由 lead 负责" in pc
    assert pc.count("⚠") == 2 and "e3" not in pc
    assert "最近一条输入来自子代理交回" in pc


def test_idle_last_and_not_counted():
    html = web.render_columns(board(busy=[sess("b")], idle=[sess("i", "idle", idle_since=NOW - 7200)]))
    assert html.index("card-busy") < html.index("card-idle-session")
    assert "◌ 空闲 2h00m" in html
    assert re.search(r'进行中 <span class="count">1</span>', html)


def test_ended_and_todos():
    e = sess("e", "ended", end=NOW - 60, duration=3700, wait_total=None, commits_today=2)
    html = web.render_columns(board(ended_today=[e], top_todos=[
        {"host": "s-host", "project": "/home/someuser/dev/x", "title": "do it", "flag": True}]))
    assert "时长 1h01m · 闲置等输入 — · 提交 2 次" in html
    assert "do it" in html and "dev/x" in html


def test_escaping():
    tasks = {"label": EVIL, "current": EVIL, "items": [{"id": EVIL, "title": EVIL, "status": "doing",
                                                       "note": EVIL}], "merges": []}
    s = dict(sess("a", "waiting", question=EVIL, last_reply=EVIL, name=EVIL, attach=EVIL,
                  project="/home/someuser/" + EVIL, errors=[EVIL], tasks=tasks,
                  last_human={"ts": 1, "text": EVIL}))
    s2 = dict(s, state="busy")
    row = lane(project="/home/someuser/" + EVIL, session_id="a", merges=[[EVIL, NOW - 10]])
    b = board(waiting=[s], busy=[s2], idle=[dict(s2)], ended_today=[dict(s2, end=NOW, duration=1)],
              top_todos=[{"host": EVIL, "project": "/home/someuser/" + EVIL, "title": EVIL}],
              lanes={"start": NOW - 4 * 3600, "end": NOW, "rows": [row], "more": 0})
    for html in (web.render_columns(b), web.render_timeline(b)):
        assert "&lt;script&gt;" in html
        for bad in ("<script", "onerror=1>", "<img", 'style="', "http://", "https://"):
            assert bad not in html


def test_busy_card_shows_running_time():
    html = web.render_columns(board(busy=[sess("a", started_at=NOW - 5400)]))
    assert "运行中 1h30m" in cards(html, "card-busy")[0]
    html = web.render_columns(board(busy=[sess("a")]))
    assert "运行中" in html and "运行中 " not in cards(html, "card-busy")[0]


def test_fold_details_has_data_key():
    big = {"label": "x", "current": "T14", "items": items(14, 13), "merges": []}
    html = web.render_columns(board(busy=[sess("a", tasks=big)]))
    assert '<details data-key="tasks-s-host-a">' in html
    evil = web.render_columns(board(busy=[sess('"><x', tasks=big)]))
    assert 'data-key="tasks-s-host-&quot;&gt;&lt;x"' in evil
