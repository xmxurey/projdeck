"""D7 in the terminal (T10): brief hosts' busy/idle projects fold into one summary line per
host in the ◉ 进行中 group, the header notes each brief host's share, the summary line's
preview lists the folded sessions, and no key acts on the summary line."""
import calendar
import copy
import json
import time

import pytest

from projdeck import board, cli, sync, ui
from projdeck.ui import strip_ansi

H = 3600
NOW = calendar.timegm((2026, 11, 18, 12, 0, 0))
HOME = "/home/u"
ALPHA, BETA, GAMMA, DELTA, EPS = (HOME + "/" + n for n in ("alpha", "beta", "gamma", "delta", "eps"))
SEG = [["busy", NOW - 2 * H, NOW - H]]
STATE_ERROR = "⚠ 状态读取失败"


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def mkcfg(brief=True):
    em = {"name": "em-host", "ssh": "em-host"}
    if brief is not None:
        em["brief"] = brief
    return {"hosts": [{"name": "s-host", "local": True}, em],
            "tiers_days": {"active": 14, "stale": 90}}


def sess(i, state, project, **kw):
    d = {"id": i, "project": project, "cwd": project, "kind": "interactive", "live": True,
         "name": "n-%s" % i, "title": "title %s" % i, "started_at": NOW - 3 * H,
         "state": state, "idle_since": NOW - 600, "question": "", "last_reply": "",
         "segments": copy.deepcopy(SEG), "attach": "", "tasks": None, "tasks_owner": None,
         "errors": [], "state_error": False}
    d.update(kw)
    return d


def base_em():
    """busy A (clean), busy B (state_error), idle C, waiting D: each in its own project."""
    return [sess("A", "busy", ALPHA),
            sess("B", "busy", BETA, state_error=True, errors=["transcript x.jsonl: boom"]),
            sess("C", "idle", GAMMA),
            sess("D", "waiting", DELTA)]


def live(host, sessions, age, projects=None):
    return {"schema": 1, "host": host, "collected_at": NOW - age, "synced_at": NOW - age,
            "stale_since": None, "last_error": None, "source": "claude agents",
            "sessions": list(sessions), "projects": projects or {}, "errors": []}


def build(em=None, cfg=None, em_age=30, em_projects=None, s=None):
    lives = [live("s-host", [sess("E", "busy", EPS)] if s is None else s, 10),
             live("em-host", base_em() if em is None else em, em_age, em_projects)]
    return board.build([], lives, mkcfg() if cfg is None else cfg, NOW)


def row(host, path, **kw):
    r = {"host": host, "path": path, "exists": True, "local": host == "s-host", "ssh": host,
         "home": HOME, "tier": "active", "activity": NOW - H, "stale_since": None,
         "last_mtime": 0, "suspicious": [], "progress": "", "errors": [], "sessions": [],
         "tag": None, "flag": False,
         "git": {"branch": "main", "dirty": 0, "ahead": 0, "untracked": 0, "no_upstream": False,
                 "last_commit_ts": NOW - 86400}}
    r.update(kw)
    return r


def rows(skip=()):
    out = [row("em-host", p) for p in (ALPHA, BETA, GAMMA, DELTA, HOME + "/quiet") if p not in skip]
    return out + [row("s-host", EPS)]


def fields(lines):
    return [ln.split(ui.SEP, 2) for ln in lines]


def entry(b, host="em-host"):
    [e] = [e for e in b["brief"]["hosts"] if e["host"] == host]
    return e


def groups(lines):
    """{group label: [field lists]} for the header lines of project_lines."""
    out, cur = {}, None
    for f in fields(lines):
        text = strip_ansi(f[2])
        if text.startswith("── "):
            cur = text[3:].rstrip("─ ").strip()
            out[cur] = []
        else:
            out[cur].append(f)
    return out


def summary_lines(lines):
    return [f for f in fields(lines) if f[1] == ui.BRIEF_FIELD]


# ---------------------------------------------------------------- 1. project_lines

def test_folded_projects_leave_the_list_and_one_summary_line_stands_for_them():
    b = build()
    lines = ui.project_lines(rows(), NOW, b)
    paths = [ui.from_field(f[1]) for f in fields(lines)]
    assert ALPHA not in paths and GAMMA not in paths  # A and C are folded
    assert BETA in paths and DELTA in paths and EPS in paths
    [s] = summary_lines(lines)
    assert s[0] == "em-host"
    assert strip_ansi(s[2]) == "◉ " + board.brief_label(entry(b))
    assert strip_ansi(s[2]) == "◉ em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1）"
    g = groups(lines)
    assert g["◉ 进行中"][-1] == s  # at the end of the in-progress group
    assert [ui.from_field(f[1]) for f in g["◉ 进行中"][:-1]] == [EPS, BETA]
    # a folded project is taken: it does not come back in its host group
    assert all(ui.from_field(f[1]) != ALPHA for f in g["em-host"])


def test_errored_session_on_brief_host_is_listed_with_the_state_error_mark():
    lines = ui.project_lines(rows(), NOW, build())
    by_path = {ui.from_field(f[1]): f[2] for f in fields(lines) if f[1] != ui.BRIEF_FIELD}
    assert strip_ansi(by_path[BETA]).endswith(STATE_ERROR)
    assert ui.YELLOW + STATE_ERROR + ui.RESET in by_path[BETA]
    # not on the non-brief host, nor on the clean ones
    assert STATE_ERROR not in strip_ansi(by_path[EPS])
    assert STATE_ERROR not in strip_ansi(by_path[DELTA])


def test_state_error_mark_only_on_brief_hosts():
    s_err = [sess("E", "busy", EPS, state_error=True)]
    lines = ui.project_lines(rows(), NOW, build(s=s_err))
    [eps] = [f for f in fields(lines) if ui.from_field(f[1]) == EPS]
    assert STATE_ERROR not in strip_ansi(eps[2])


@pytest.mark.parametrize("age,yellow", [(400, True), (100, False)])
def test_summary_line_is_yellow_with_data_time_when_aging(age, yellow):
    b = build(em_age=age)
    [s] = summary_lines(ui.project_lines(rows(), NOW, b))
    text = strip_ansi(s[2])
    assert text.startswith("◉ " + board.brief_label(entry(b)))
    if yellow:
        assert s[2].startswith(ui.YELLOW) and s[2].endswith(ui.RESET)
        assert text.endswith(" · 数据 11:53（6m 前）")  # 12:00:00 - 400 s, our own clock
    else:
        assert ui.YELLOW not in s[2] and "数据" not in text


def test_stale_brief_host_has_no_summary_line_and_lists_its_sessions():
    lines = ui.project_lines(rows(), NOW, build(em_age=700))
    assert summary_lines(lines) == []
    texts = {ui.from_field(f[1]): strip_ansi(f[2]) for f in fields(lines)}
    assert "⚠ live 数据" in texts[ALPHA] and "⚠ live 数据" in texts[GAMMA]


def test_group_header_stays_when_only_the_summary_line_is_left():
    b = build(em=[sess("A", "busy", ALPHA)], s=[])
    g = groups(ui.project_lines(rows(), NOW, b))
    assert [f[1] for f in g["◉ 进行中"]] == [ui.BRIEF_FIELD]


def test_project_lines_follow_board_folded_keys():
    b = build()
    b["brief"]["keys"] = [["em-host", "B"]]
    paths = [ui.from_field(f[1]) for f in fields(ui.project_lines(rows(), NOW, b))]
    assert BETA not in paths and ALPHA in paths and GAMMA in paths


def test_folded_busy_and_errored_idle_in_one_project_listed_by_the_idle_one():
    em = [sess("A", "busy", ALPHA), sess("F", "idle", ALPHA, state_error=True)]
    b = build(em=em)
    assert board.folded(b, next(s for s in b["busy"] if s["id"] == "A"))
    lines = ui.project_lines(rows(), NOW, b)
    [alpha] = [f for f in groups(lines)["◉ 进行中"] if ui.from_field(f[1]) == ALPHA]
    text = strip_ansi(alpha[2])
    assert text.startswith("◌") and "空闲 10m" in text and text.endswith(STATE_ERROR)
    _, rep = ui.preview_target(b, rows(), "em-host", ALPHA, NOW)
    assert rep["id"] == "F"


def test_waiting_row_carries_the_mark_when_its_project_has_an_errored_idle():
    # D (waiting) speaks for DELTA; F, an idle in the same project, could not be read. The
    # summary says F is listed apart, so DELTA's row has to say so too.
    em = [sess("D", "waiting", DELTA), sess("F", "idle", DELTA, state_error=True)]
    b = build(em=em)
    assert ui.preview_target(b, rows(), "em-host", DELTA, NOW)[1]["id"] == "D"
    [delta] = [f for f in groups(ui.project_lines(rows(), NOW, b))["◆ 等你"]
               if ui.from_field(f[1]) == DELTA]
    assert strip_ansi(delta[2]).endswith(STATE_ERROR)
    assert ui.YELLOW + STATE_ERROR + ui.RESET in delta[2]


def test_unkeyed_busy_row_carries_the_mark_when_its_project_has_an_errored_idle():
    em = [sess(None, "busy", ALPHA), sess("F", "idle", ALPHA, state_error=True)]
    b = build(em=em)
    assert ui.preview_target(b, rows(), "em-host", ALPHA, NOW)[1]["id"] is None
    [alpha] = [f for f in groups(ui.project_lines(rows(), NOW, b))["◉ 进行中"]
               if ui.from_field(f[1]) == ALPHA]
    assert strip_ansi(alpha[2]).endswith(STATE_ERROR)


def test_waiting_row_without_an_errored_neighbour_has_no_mark():
    em = [sess("D", "waiting", DELTA), sess("F", "idle", DELTA)]
    [delta] = [f for f in groups(ui.project_lines(rows(), NOW, build(em=em)))["◆ 等你"]
               if ui.from_field(f[1]) == DELTA]
    assert STATE_ERROR not in strip_ansi(delta[2])


def test_waiting_row_on_a_non_brief_host_has_no_mark_for_an_errored_neighbour():
    s = [sess("D", "waiting", EPS), sess("F", "idle", EPS, state_error=True)]
    [eps] = [f for f in groups(ui.project_lines(rows(), NOW, build(s=s)))["◆ 等你"]
             if ui.from_field(f[1]) == EPS]
    assert STATE_ERROR not in strip_ansi(eps[2])


# ---------------------------------------------------------------- 2. header and speakers

def test_header_notes_brief_hosts_share():
    first = strip_ansi(ui.header_lines(build(), NOW)[0])
    assert "◉ 进行中 3（em-host 2）" in first
    assert "◌ 空闲 1（em-host 1）" in first


def test_speakers_on_a_non_brief_host_pick_as_before():
    s = [sess("E1", "busy", EPS), sess("E2", "idle", EPS), sess("E3", "waiting", EPS),
         sess("E4", "busy", EPS), sess("G1", "idle", GAMMA), sess("G2", "busy", GAMMA),
         sess("G3", "sub", GAMMA)]
    for cfg in (mkcfg(), mkcfg(None)):
        b = build(s=s, cfg=cfg)
        assert ui.preview_target(b, rows(), "s-host", EPS, NOW)[1]["id"] == "E3"
        # busy and sub tie: the one the board lists first (sub before busy) speaks
        assert ui.preview_target(b, rows(), "s-host", GAMMA, NOW)[1]["id"] == "G3"


@pytest.mark.parametrize("cfg", [mkcfg(False), mkcfg(None)])
def test_no_brief_terminal_output_matches_without_brief_key(cfg):
    b = build(cfg=cfg)
    plain = copy.deepcopy(b)
    del plain["brief"]
    assert ui.project_lines(rows(), NOW, b) == ui.project_lines(rows(), NOW, plain)
    assert ui.header_lines(b, NOW) == ui.header_lines(plain, NOW)
    assert ui.project_lines(rows(), NOW, b) == ui.project_lines(rows(), NOW, build(cfg=mkcfg(False)))
    assert "（" not in strip_ansi(ui.header_lines(b, NOW)[0])


# ---------------------------------------------------------------- project_flags

def test_project_flags_are_the_project_line_flags():
    r = row("em-host", ALPHA, git={"branch": "m", "dirty": 3, "ahead": 2, "untracked": 0,
                                   "no_upstream": True, "last_commit_ts": NOW},
            suspicious=["x.log"], sessions=[{"id": "s", "last_ts": NOW, "title": "t"}])
    flags = ui.project_flags(r)
    assert flags == ["±3", "↑2", "⊘", "⚑1", "💬1"]
    assert strip_ansi(ui.project_line(r, NOW)).endswith("  " + " ".join(flags))


def test_project_flags_git_given_by_caller():
    ok = row("em-host", ALPHA)
    failed = row("em-host", ALPHA, git=None, errors=["git: boom"])
    assert "git?" not in ui.project_flags(ok)
    assert "git?" in ui.project_flags(ok, git=True)
    assert "git?" in ui.project_flags(failed)
    assert "git?" not in ui.project_flags(failed, git=False)
    assert "non-git" not in ui.project_flags(failed, git=False)  # failed git is not "no git"
    assert "non-git" in ui.project_flags(row("em-host", ALPHA, git=None))


# ---------------------------------------------------------------- 3. the summary preview

def preview_blocks(text):
    """Session blocks of brief_preview_text: (head line, [lines below it])."""
    lines = text.split("\n")
    out = []
    for ln in lines:
        plain = strip_ansi(ln)
        if plain[:1] in ("◉", "◌"):
            out.append((plain, []))
        elif out:
            out[-1][1].append(ln)
    return lines, out


def test_brief_preview_lists_folded_sessions_with_their_timeline():
    b = build()
    lines, blocks = preview_blocks(ui.brief_preview_text(b, rows(), "em-host", NOW))
    assert lines[0] == board.brief_label(entry(b))
    assert lines[1].startswith("── 最近 6 小时 ── 06:00 ─── 12:00 ──")
    assert [h for h, _ in blocks] == ["◉ alpha · n-A · 运行中 3h00m", "◌ gamma · n-C · 空闲 10m"]
    for _, below in blocks:
        # 48 cells of 7.5 min from 06:00; SEG (10:00-11:00) covers cells 32..39
        assert strip_ansi(below[0]) == "▆" * 48
        assert below[0].count("57;135;229") == 8
        assert below[0].split("▆")[32].endswith("\x1b[38;2;57;135;229m")
    assert all("B" not in h for h, _ in blocks)


def test_brief_preview_shows_warnings_git_and_live_only_projects():
    em = base_em()
    em[0]["errors"] = ["TaskUpdate: unknown taskId 7"]
    em[0]["tasks"] = {"items": [{"id": "T1", "status": "done"}, {"id": "T2", "status": "doing"}]}
    b = build(em=em, em_projects={ALPHA: {"error": "git: boom", "commits": None}})
    assert board.folded(b, next(s for s in b["busy"] if s["id"] == "A"))
    assert "git 读取失败 1 个项目" in board.brief_label(entry(b))
    _, blocks = preview_blocks(ui.brief_preview_text(b, rows(skip=(GAMMA,)), "em-host", NOW))
    (a_head, a_below), (c_head, c_below) = blocks
    assert a_head.endswith("  git?")
    assert strip_ansi(a_below[1]) == "■▣ 1/2 T2"
    assert a_below[2] == "  ⚠ TaskUpdate: unknown taskId 7"
    assert c_head == "◌ gamma · n-C · 空闲 10m"  # only in the live data: a minimal row
    assert "git?" not in c_head and len(c_below) == 1


def test_brief_preview_warns_about_aging_data():
    text = ui.brief_preview_text(build(em_age=400), rows(), "em-host", NOW)
    second = text.split("\n")[1]
    assert second == ui.YELLOW + "⚠ live 数据 11:53（6m 前），会话状态可能已过期" + ui.RESET


def test_brief_preview_follows_board_folded_keys():
    b = build()
    b["brief"]["keys"] = [["em-host", "C"]]
    _, blocks = preview_blocks(ui.brief_preview_text(b, rows(), "em-host", NOW))
    assert [h for h, _ in blocks] == ["◌ gamma · n-C · 空闲 10m"]


def test_brief_preview_of_a_host_without_summary_says_so():
    text = ui.brief_preview_text(build(cfg=mkcfg(False)), rows(), "em-host", NOW)
    assert text == "em-host：没有精简展示的会话"


# ---------------------------------------------------------------- cli: _preview and browse

def test_cli_preview_of_the_summary_line(tmp_path, monkeypatch, capsys):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(dict(mkcfg(), trash_dir=str(tmp_path / "trash"))))
    monkeypatch.setenv("PROJ_CONFIG", str(cfgp))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    em = base_em()
    em[0]["errors"] = ["ledger: boom"]
    sync._write_json(sync._live_path("em-host"), live("em-host", em, 30))
    sync._write_json(sync._live_path("s-host"), live("s-host", [sess("E", "busy", EPS)], 10))
    assert cli.main(["_preview", "em-host", ui.BRIEF_FIELD + "\t"]) == 0
    out = capsys.readouterr().out.rstrip("\n").split("\n")
    assert out[0] == "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1） · 告警 1"
    heads = [strip_ansi(x) for x in out if strip_ansi(x)[:1] in ("◉", "◌")]
    assert heads == ["◉ alpha · n-A · 运行中 3h00m", "◌ gamma · n-C · 空闲 10m"]
    assert "  ⚠ ledger: boom" in out


def browse(monkeypatch, picks):
    cfg = {"hosts": [{"name": "em-host", "ssh": "em-host", "brief": True}],
           "tiers_days": {"active": 14, "stale": 90}}
    cache = {"host": "em-host", "projects": [row("em-host", BETA)]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: [cache])
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: [live("em-host", base_em(), 30)])
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: [cache])
    picks, seen, acted = iter(picks), [], []

    def fake_pick(rows_, now, notice=None, select=None, board=None):
        seen.append(notice)
        try:
            return next(picks)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", fake_pick)
    monkeypatch.setattr(cli, "launch", lambda *a, **k: acted.append("launch") or "iterm")
    monkeypatch.setattr(cli, "open_project", lambda *a, **k: acted.append("open") or ("iterm", None))
    monkeypatch.setattr(cli, "clean", lambda *a, **k: acted.append("clean") or "cleaned")
    monkeypatch.setattr(ui, "pick_session", lambda *a, **k: acted.append("pick_session"))
    assert cli.cmd_browse(cfg, None) == 0
    return seen, acted


@pytest.mark.parametrize("key", ["", "ctrl-x", "ctrl-r", "ctrl-y"])
def test_keys_on_the_summary_line_do_nothing(monkeypatch, key):
    seen, acted = browse(monkeypatch, [(key, "em-host", ui.BRIEF_FIELD)])
    assert acted == []
    assert seen == [None, None]


def test_keys_on_a_project_line_still_act(monkeypatch):
    _, acted = browse(monkeypatch, [("", "em-host", BETA)])
    assert acted == ["open"]


def test_no_new_keys():
    assert ui.PROJECT_KEYS == ["ctrl-r", "ctrl-y", "ctrl-f", "ctrl-x", "ctrl-u", "ctrl-l", "ctrl-v"]
    assert ui.BRIEF_FIELD == "brief:"
    assert not hasattr(ui, "folded") and not hasattr(cli, "folded")
