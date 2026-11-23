"""Independent spec tests for T10: terminal fold of brief hosts' busy/idle projects into one
summary row per host, the header note, the representative rule, `_preview <host> brief:` and
the inert summary row. Expected values are derived by hand from the brief and the rulings.

Fixture (host em-host has brief on): A busy /home/u/alpha, B busy+state_error /home/u/beta,
C idle /home/u/gamma, D waiting /home/u/delta (own project: a waiting session represents its
project in the waiting group, so A and C live in projects without a waiting session). s-host
is not a brief host and has E busy /home/u/eps."""
import ast
import calendar
import copy
import json
import os
import re
import site
import subprocess
import sys
import time

import pytest

from projdeck import board, cli, config, model, ui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = calendar.timegm((2026, 11, 18, 12, 0, 0))
ALPHA, BETA, GAMMA, DELTA, EPS = ("/home/u/" + n for n in ("alpha", "beta", "gamma", "delta", "eps"))
LABEL = "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1）"
YELLOW = re.compile(r"\x1b\[(?:[0-9;]*;)?(?:33|93)m|38;5;(?:3|11|172|178|214|220|221|226|227)m"
                    r"|38;2;(?:2[0-5]\d|1[89]\d);(?:1[5-9]\d|2[0-5]\d);(?:[0-9]|[1-9]\d)m")


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def plain(s):
    return ui.strip_ansi(s)


def sess(i, state, project, now=NOW, **kw):
    d = {"id": i, "project": project, "cwd": project, "latest_cwd": project,
         "kind": "interactive", "live": True, "name": "n-" + i, "title": "", "started_at": now - 20000,
         "status": "busy" if state in ("busy", "sub") else "idle", "state": state,
         "idle_since": now - 600 if state != "busy" else None,
         "question": "Q?" if state == "waiting" else "", "last_reply": "",
         "last_human": {"ts": 0, "text": ""}, "last_input": "human", "subagents_running": 0,
         "segments": [["busy", now - 7200, now - 60]], "attach": "", "tasks": None,
         "tasks_owner": None, "errors": [], "state_error": False}
    d.update(kw)
    return d


def proj(path, now=NOW):
    return {"path": path, "exists": True, "git": None, "suspicious": [], "progress": "",
            "errors": [], "sessions": [], "last_mtime": now - 100}


def cache(host, paths, now=NOW):
    return {"schema": 1, "host": host, "collected_at": now - 60, "synced_at": now - 60,
            "stale_since": None, "last_error": None, "projects": [proj(p, now) for p in paths]}


def live(host, sessions, at, projects=None):
    return {"schema": 1, "host": host, "collected_at": at, "synced_at": at, "stale_since": None,
            "last_error": None, "source": "claude agents", "since": at - 86400,
            "sessions": [dict(s, host=host) for s in sessions], "projects": projects or {},
            "zellij_exited": None, "errors": []}


def cfg(em_brief=True, s_brief=None):
    em = {"name": "em-host", "ssh": "em-host"}
    if em_brief is not None:
        em["brief"] = em_brief
    s = {"name": "s-host", "ssh": "s-host"}
    if s_brief is not None:
        s["brief"] = s_brief
    c = copy.deepcopy(config.DEFAULTS)
    c["hosts"] = [s, em]
    return c


def em_sessions(now=NOW):
    return [sess("A", "busy", ALPHA, now), sess("B", "busy", BETA, now, state_error=True),
            sess("C", "idle", GAMMA, now), sess("D", "waiting", DELTA, now)]


def world(em_brief=True, s_brief=None, em_age=30, em=None, extra_paths=(), now=NOW, em_projects=None):
    c = cfg(em_brief, s_brief)
    caches = [cache("s-host", [EPS], now),
              cache("em-host", [ALPHA, BETA, GAMMA, DELTA] + list(extra_paths), now)]
    lives = [live("s-host", [sess("E", "busy", EPS, now)], now - 10),
             live("em-host", em_sessions(now) if em is None else em, now - em_age,
                  em_projects)]
    b = board.build(caches, lives, c, now)
    rows = model.rows(caches, c, now)
    return b, rows, c, caches, lives


def lines_of(rows, b, now=NOW):
    return [ln.split("\t") for ln in ui.project_lines(rows, now, board=b)]


def find(lines, path):
    return [i for i, (_, p, _) in enumerate(lines) if p == path]


def hdr(lines, text):
    hits = [i for i, (_, p, d) in enumerate(lines) if text in plain(d) and p == ""]
    assert len(hits) == 1, (text, [plain(d) for _, _, d in lines])
    return hits[0]


def hhmm(ts):
    return time.strftime("%H:%M", time.gmtime(ts))


def brief_rows(lines):
    return [i for i, (_, p, _) in enumerate(lines) if p == "brief:"]


# ---------------------------------------------------------------- AC1: the rows

def test_interface_constants_and_signatures():
    assert ui.BRIEF_FIELD == "brief:"
    assert callable(ui.project_flags) and callable(ui.brief_preview_text)


def test_folded_projects_have_no_row_and_one_summary_row_exists():
    b, rows, *_ = world()
    lines = lines_of(rows, b)
    assert find(lines, ALPHA) == [] and find(lines, GAMMA) == []
    bi = brief_rows(lines)
    assert len(bi) == 1
    host, field, disp = lines[bi[0]]
    assert (host, field) == ("em-host", "brief:")
    # the same sentence the web summary uses, built by the one shared function
    assert board.brief_label(b["brief"]["hosts"][0]) == LABEL
    assert LABEL in plain(disp)
    assert "\n" not in disp


def test_summary_row_sits_in_in_progress_group_before_host_groups():
    b, rows, *_ = world()
    lines = lines_of(rows, b)
    i = brief_rows(lines)[0]
    h = hdr(lines, "── ◉ 进行中 ──")
    assert h < i and all(lines[k][1] != "" for k in range(h + 1, i + 1))  # same group, no other header


def test_errored_project_stays_in_in_progress_group_with_failure_mark():
    b, rows, *_ = world()
    lines = lines_of(rows, b)
    idx = find(lines, BETA)
    assert len(idx) == 1 and idx[0] > hdr(lines, "── ◉ 进行中 ──")
    assert "⚠ 状态读取失败" in plain(lines[idx[0]][2])
    assert plain(lines[idx[0]][2]).startswith("◉")
    # no mark on the others: non-brief host's busy project, the waiting project
    for p in (EPS, DELTA):
        d = plain(lines[find(lines, p)[0]][2])
        assert "状态读取失败" not in d, p


def test_waiting_session_keeps_its_project_in_waiting_group_unfolded():
    b, rows, *_ = world()
    lines = lines_of(rows, b)
    i = find(lines, DELTA)
    assert len(i) == 1
    assert hdr(lines, "── ◆ 等你 ──") < i[0] < hdr(lines, "── ◉ 进行中 ──")


def test_no_summary_row_when_host_not_brief_and_no_failure_mark():
    for flag in (None, False):
        b, rows, *_ = world(em_brief=flag)
        lines = lines_of(rows, b)
        assert brief_rows(lines) == []
        assert find(lines, ALPHA) and find(lines, GAMMA) and find(lines, BETA)
        assert all("状态读取失败" not in plain(d) for _, _, d in lines)


def test_brief_absent_and_false_render_byte_identical():
    a = lines_of(*world(em_brief=None)[:2][::-1])
    f = lines_of(*world(em_brief=False)[:2][::-1])
    assert a == f


def test_non_brief_host_rows_do_not_change_when_other_host_is_brief():
    on = lines_of(*world(em_brief=True)[:2][::-1])
    off = lines_of(*world(em_brief=False)[:2][::-1])
    for p in (EPS,):
        assert on[find(on, p)[0]] == off[find(off, p)[0]]


def test_aging_host_summary_is_yellow_and_names_the_data_time():
    b, rows, *_ = world(em_age=400)
    lines = lines_of(rows, b)
    disp = lines[brief_rows(lines)[0]][2]
    assert YELLOW.search(disp), repr(disp)
    assert "数据 " + hhmm(NOW - 400) in plain(disp)
    assert LABEL in plain(disp)


def test_fresh_host_summary_is_not_yellow_and_has_no_data_time():
    b, rows, *_ = world(em_age=30)
    lines = lines_of(rows, b)
    disp = lines[brief_rows(lines)[0]][2]
    assert not YELLOW.search(disp), repr(disp)
    assert "数据 " not in plain(disp)


def test_stale_host_has_no_summary_row_and_sessions_listed_one_by_one():
    b, rows, *_ = world(em_age=5000)
    lines = lines_of(rows, b)
    assert brief_rows(lines) == []
    assert find(lines, ALPHA) and find(lines, GAMMA)


def test_summary_row_counts_follow_the_board_not_a_copy():
    # one more folded busy session in its own project: label says 进行中 2
    b, rows, *_ = world(em=em_sessions() + [sess("F", "busy", "/home/u/zeta")],
                        extra_paths=["/home/u/zeta"])
    lines = lines_of(rows, b)
    assert find(lines, "/home/u/zeta") == []
    assert "em-host · 进行中 2 · 空闲 1（另有单独列出的 进行中 1：出错 1）" in plain(
        lines[brief_rows(lines)[0]][2])


def test_zero_count_host_gets_no_summary_row():
    b, rows, *_ = world(em=[sess("D", "waiting", DELTA)])
    assert brief_rows(lines_of(rows, b)) == []


def test_untrusted_text_in_label_cannot_inject_escapes():
    # session ids and errors are external text; none may reach the row raw
    ev = "\x1b]0;pwn\x07"
    b, rows, *_ = world(em=em_sessions() + [sess("F" + ev, "busy", "/home/u/zeta",
                                                    errors=[ev])], extra_paths=["/home/u/zeta"])
    assert all("\x1b]0;" not in ln for ln in ui.project_lines(rows, NOW, board=b))


# ---------------------------------------------------------------- AC2: header, representative

def test_header_counts_carry_the_brief_host_share():
    b, *_ = world()
    l1 = plain(ui.header_lines(b, NOW)[0])
    assert "◉ 进行中 3（em-host 2）" in l1
    assert "◌ 空闲 1（em-host 1）" in l1


def test_header_total_still_includes_brief_host_and_no_note_without_brief():
    b, *_ = world(em_brief=None)
    l1 = plain(ui.header_lines(b, NOW)[0])
    assert "◉ 进行中 3" in l1 and "◌ 空闲 1" in l1
    assert not re.search(r"(进行中|空闲) \d+（", l1)


def test_header_two_brief_hosts_joined_with_middle_dot():
    b, *_ = world(s_brief=True)
    l1 = plain(ui.header_lines(b, NOW)[0])
    m = re.search(r"◉ 进行中 3（([^）]*)）", l1)
    assert m, l1
    assert set(m.group(1).split(" · ")) == {"em-host 2", "s-host 1"}
    assert len(m.group(1).split(" · ")) == 2
    assert "◌ 空闲 1（em-host 1）" in l1


def mixed_world(**kw):
    """alpha holds a folded busy A and an errored idle F: the idle one must represent it."""
    em = [sess("A", "busy", ALPHA), sess("F", "idle", ALPHA, state_error=True)]
    return world(em=em, **kw)


def test_errored_idle_represents_project_with_folded_busy_sibling():
    b, rows, *_ = mixed_world()
    lines = lines_of(rows, b)
    idx = find(lines, ALPHA)
    assert len(idx) == 1 and idx[0] > hdr(lines, "── ◉ 进行中 ──")
    d = plain(lines[idx[0]][2])
    assert d.startswith("◌") and "⚠ 状态读取失败" in d
    # the busy sibling is still folded into the summary
    assert "进行中 1" in plain(lines[brief_rows(lines)[0]][2])


def test_preview_target_picks_errored_idle_over_folded_busy():
    b, rows, *_ = mixed_world()
    r, lv = ui.preview_target(b, rows, "em-host", ALPHA, NOW)
    assert r is not None and lv["id"] == "F"


def multi(em_brief):
    em = [sess("i", "idle", ALPHA), sess("b", "busy", ALPHA),
          sess("w", "waiting", BETA), sess("b2", "busy", BETA),
          sess("i2", "idle", GAMMA), sess("i3", "idle", GAMMA, idle_since=NOW - 9000)]
    return world(em_brief=em_brief, em=em)


@pytest.mark.parametrize("path,want", [(ALPHA, "b"), (BETA, "w")])
def test_non_brief_host_representative_is_most_urgent(path, want):
    for flag in (None, False):
        b, rows, *_ = multi(flag)
        assert ui.preview_target(b, rows, "em-host", path, NOW)[1]["id"] == want


def test_non_brief_project_lines_for_multi_session_projects_ignore_brief_key():
    assert lines_of(*multi(None)[:2][::-1]) == lines_of(*multi(False)[:2][::-1])


def test_other_hosts_representative_unaffected_by_a_brief_host():
    off = multi(None)
    on = multi(True)
    # s-host project eps has one session; its representative is the same either way
    assert ui.preview_target(on[0], on[1], "s-host", EPS, NOW)[1]["id"] == \
        ui.preview_target(off[0], off[1], "s-host", EPS, NOW)[1]["id"] == "E"


def test_project_flags_returns_list_of_strings_and_default_is_git_none():
    _, rows, *_ = world()
    for r in rows:
        f = ui.project_flags(r)
        assert isinstance(f, list) and all(isinstance(x, str) for x in f)
        assert f == ui.project_flags(r, git=None)


# ---------------------------------------------------------------- AC3: brief_preview_text

def test_brief_preview_text_lists_folded_sessions_only():
    b, rows, *_ = world()
    out = plain(ui.brief_preview_text(b, rows, "em-host", NOW))
    assert LABEL in out
    assert out.index(LABEL) < out.index("alpha") < out.index("gamma")
    assert "beta" not in out and "delta" not in out and "eps" not in out


def test_brief_preview_timeline_is_48_cells_under_each_session():
    b, rows, *_ = world()
    out = plain(ui.brief_preview_text(b, rows, "em-host", NOW)).split("\n")
    for name in ("alpha", "gamma"):
        i = [k for k, ln in enumerate(out) if name in ln]
        assert len(i) == 1, name
        assert len(out[i[0] + 1].strip()) == 48, repr(out[i[0] + 1])
    a = [k for k, ln in enumerate(out) if "alpha" in ln][0]
    assert "▆" in out[a + 1]


def test_brief_preview_marks_alarm_and_git_failure():
    em = [sess("A", "busy", ALPHA, errors=["disk hiccup"]), sess("C", "idle", GAMMA)]
    b, rows, *_ = world(em=em, em_projects={ALPHA: {"error": "boom", "commits": None}})
    out = plain(ui.brief_preview_text(b, rows, "em-host", NOW)).split("\n")
    a = [k for k, ln in enumerate(out) if "alpha" in ln][0]
    assert "git?" in out[a]
    assert "⚠ disk hiccup" in out[a + 2]
    g = [k for k, ln in enumerate(out) if "gamma" in ln][0]
    assert "git?" not in out[g] and not any("⚠" in ln for ln in out[g:])


def test_brief_preview_without_alarm_or_git_failure_has_neither():
    b, rows, *_ = world()
    out = plain(ui.brief_preview_text(b, rows, "em-host", NOW))
    assert "git?" not in out and "disk" not in out


def test_brief_preview_live_only_project_does_not_crash():
    # rows know nothing of gamma: only live data does
    b, rows, *_ = world()
    rows = [r for r in rows if r["path"] != GAMMA]
    out = plain(ui.brief_preview_text(b, rows, "em-host", NOW))
    assert "gamma" in out and "alpha" in out


def test_brief_preview_uses_board_fold_not_its_own_rule(monkeypatch):
    b, rows, *_ = world()
    monkeypatch.setattr(board, "folded", lambda bd, s: s.get("id") == "C")
    out = plain(ui.brief_preview_text(b, rows, "em-host", NOW))
    assert "gamma" in out and "alpha" not in out


def test_project_lines_use_board_fold_verdict(monkeypatch):
    b, rows, *_ = world()
    monkeypatch.setattr(board, "folded", lambda bd, s: False)
    lines = lines_of(rows, b)
    assert find(lines, ALPHA) and find(lines, GAMMA)


def test_ui_and_cli_call_board_by_module_attribute():
    names = {"folded", "session_error", "brief_label", "brief_note", "brief_age"}
    for fn in ("ui.py", "cli.py"):
        with open(os.path.join(ROOT, "projdeck", fn), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        bad = [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and (n.module or "").split(".")[-1] == "board" for a in n.names if a.name in names]
        assert bad == [], fn


# ---------------------------------------------------------------- AC3: `_preview em-host brief:` end to end

@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cdir = tmp_path / "cache"
    (cdir / "live").mkdir(parents=True)
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"hosts": [{"name": "em-host", "ssh": "em-host", "brief": True}]}))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for prog, code in (("ssh", 255), ("zellij", 1), ("tmux", 1), ("tailscale", 1),
                       ("systemctl", 1), ("fzf", 1), ("claude", 0)):
        s = stubs / prog
        s.write_text("#!/bin/sh\n" + ("echo '[]'\n" if prog == "claude" else "") + "exit %d\n" % code)
        s.chmod(0o755)
    e = dict(os.environ)
    e.update(HOME=str(home), PROJ_CACHE=str(cdir), PROJ_CONFIG=str(cfgp),
             PYTHONUSERBASE=site.USER_BASE, PATH=str(stubs) + os.pathsep + os.environ["PATH"])
    e.pop("PROJ_LIVE_TESTS", None)
    return {"env": e, "cache": cdir}


def put(env, paths, sessions, projects=None):
    t = int(time.time())
    (env["cache"] / "em-host.json").write_text(json.dumps(cache("em-host", paths, t)))
    (env["cache"] / "live" / "em-host.json").write_text(
        json.dumps(live("em-host", sessions, t, projects)))
    return t


def run_preview(env, *argv):
    r = subprocess.run([sys.executable, "-m", "projdeck", "_preview", *argv], cwd=ROOT,
                       env=env["env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def e2e_sessions(t, **a_kw):
    long = [["busy", t - 21600, t - 60]]
    return [sess("A", "busy", ALPHA, t, segments=long, **a_kw),
            sess("B", "busy", BETA, t, state_error=True),
            sess("C", "idle", GAMMA, t, segments=long),
            sess("D", "waiting", DELTA, t)]


def test_e2e_summary_then_two_sessions_with_timelines(env):
    t = int(time.time())
    put(env, [ALPHA, BETA, GAMMA, DELTA], e2e_sessions(t))
    code, out, err = run_preview(env, "em-host", "brief:")
    assert code == 0, err
    out = ui.strip_ansi(out)
    assert LABEL in out
    lines = out.split("\n")
    a = [k for k, ln in enumerate(lines) if "alpha" in ln]
    c = [k for k, ln in enumerate(lines) if "gamma" in ln]
    assert len(a) == 1 and len(c) == 1
    assert out.index(LABEL) < out.index("alpha") < out.index("gamma")
    for k in (a[0], c[0]):
        assert len(lines[k + 1].strip()) == 48, repr(lines[k + 1])
    assert "▆" in lines[a[0] + 1]
    assert "beta" not in out and "delta" not in out
    assert "还没采集" not in out and "(not in cache)" not in out


def test_e2e_alarm_and_git_failure_marks(env):
    t = int(time.time())
    put(env, [ALPHA, BETA, GAMMA, DELTA], e2e_sessions(t, errors=["disk hiccup"]),
        projects={ALPHA: {"error": "boom", "commits": None}})
    code, out, err = run_preview(env, "em-host", "brief:")
    assert code == 0, err
    lines = ui.strip_ansi(out).split("\n")
    a = [k for k, ln in enumerate(lines) if "alpha" in ln][0]
    assert "git?" in lines[a]
    assert "⚠ disk hiccup" in lines[a + 2]
    assert "告警 1" in out and "git 读取失败 1 个项目" in out


def test_e2e_live_only_project_still_previewed(env):
    t = int(time.time())
    put(env, [ALPHA, BETA, DELTA], e2e_sessions(t))  # gamma is not in the cache
    code, out, err = run_preview(env, "em-host", "brief:")
    assert code == 0, err
    assert "gamma" in ui.strip_ansi(out) and "Traceback" not in err


def test_e2e_escape_sequences_never_reach_output(env):
    t = int(time.time())
    put(env, [ALPHA, BETA, GAMMA, DELTA], e2e_sessions(t, errors=["x\x1b]0;pwn\x07y"]))
    code, out, err = run_preview(env, "em-host", "brief:")
    assert code == 0, err
    assert "\x1b]0;" not in out and "\x07" not in out


def test_e2e_normal_project_preview_unchanged_by_brief_key(env):
    t = int(time.time())
    put(env, [ALPHA, BETA, GAMMA, DELTA], e2e_sessions(t))
    code, out, err = run_preview(env, "em-host", ALPHA)
    assert code == 0, err
    assert "em-host:" + ALPHA in ui.strip_ansi(out)


# ---------------------------------------------------------------- AC4: the summary row is inert

def browse(monkeypatch, picks):
    now = int(time.time())
    b, rows, c, caches, lives = world(now=now)
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg_, n: caches)
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg_: caches)
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg_, n: lives)
    monkeypatch.setattr(cli, "background_sync", lambda *a, **k: None)
    calls = []

    def rec(name, ret=None):
        return lambda *a, **k: calls.append(name) or ret
    monkeypatch.setattr(cli, "launch", rec("launch", "here"))
    monkeypatch.setattr(cli, "clean", rec("clean"))
    monkeypatch.setattr(cli, "open_project", rec("open_project", ("here", None)))
    it = iter(picks)
    seen = []

    def fake_pick(rows_, now_, notice=None, select=None, board=None):
        seen.append({"notice": notice, "select": select, "board": board})
        try:
            return next(it)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", fake_pick)
    rc = cli.cmd_browse(c, None)
    return seen, calls, rc


@pytest.mark.parametrize("key", ["", "ctrl-x", "ctrl-r", "ctrl-y"])
def test_summary_row_keys_do_nothing_and_say_nothing(monkeypatch, key):
    seen, calls, rc = browse(monkeypatch, [(key, "em-host", "brief:")])
    assert rc == 0
    assert calls == []
    assert len(seen) == 2  # the list came back
    assert "还没采集" not in " ".join(str(s["notice"]) for s in seen)
    assert seen[0]["board"]["brief"]["hosts"]  # sanity: a brief board reached the picker


def test_real_project_row_still_acts_after_summary_row_was_ignored(monkeypatch):
    seen, calls, rc = browse(monkeypatch, [("", "em-host", "brief:"), ("ctrl-x", "em-host", BETA)])
    assert calls == ["clean"]


def test_project_keys_unchanged():
    assert ui.PROJECT_KEYS == ["ctrl-r", "ctrl-y", "ctrl-f", "ctrl-x", "ctrl-u", "ctrl-l", "ctrl-v"]
