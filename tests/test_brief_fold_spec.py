"""Spec-derived tests for T08: board.brief_fold / folded / session_error / brief_note /
brief_label / brief_age and lanes["brief"]. Every expected value is counted by hand from the
fixture and the brief / rulings, never copied from an implementation run."""
import calendar
import copy
import json
import os
import re
import time

import pytest

from projdeck import board

H = 3600
NOW = calendar.timegm((2026, 11, 18, 12, 0, 0))
ALPHA = "/home/u/alpha"
BETA = "/home/u/beta"
GAMMA = "/home/u/gamma"
SEG = [["busy", NOW - 2 * H, NOW - H]]
COUNT_KEYS = ("folded_busy", "folded_idle", "errored_busy", "errored_idle", "unkeyed_busy",
              "unkeyed_idle", "busy_all", "idle_all", "warned", "git_failed_projects")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def cfg(em_brief=True, s_brief=None):
    em = {"name": "em-host", "ssh": "em-host"}
    if em_brief is not None:
        em["brief"] = em_brief
    s = {"name": "s-host", "local": True}
    if s_brief is not None:
        s["brief"] = s_brief
    return {"hosts": [s, em]}


def sess(i, state, project=ALPHA, **kw):
    d = {"id": i, "project": project, "cwd": project, "latest_cwd": project,
         "kind": "interactive", "live": True, "name": "n-%s" % i, "title": "",
         "started_at": NOW - 3 * H, "status": "busy" if state in ("busy", "sub") else "idle",
         "state": state, "idle_since": NOW - 100, "question": "", "last_reply": "",
         "last_human": {"ts": 0, "text": ""}, "last_input": "human", "subagents_running": 0,
         "segments": copy.deepcopy(SEG), "attach": "", "tasks": None, "tasks_owner": None,
         "errors": [], "state_error": False}
    d.update(kw)
    return d


def live(host, sessions, age, projects=None, **kw):
    d = {"schema": 1, "host": host, "collected_at": NOW - age, "synced_at": NOW - age,
         "stale_since": None, "last_error": None, "source": "claude agents",
         "since": NOW - age - 86400, "sessions": list(sessions), "projects": projects or {},
         "errors": []}
    d.update(kw)
    return d


def cache(host):
    return {"schema": 1, "host": host, "collected_at": NOW - 60, "synced_at": NOW - 60,
            "stale_since": None, "last_error": None, "projects": []}


def base_em():
    """em-host: busy A (clean), busy B (state_error), idle C, waiting D."""
    return [sess("A", "busy"),
            sess("B", "busy", project=BETA, state_error=True,
                 errors=["transcript /home/u/x.jsonl: boom"]),
            sess("C", "idle"),
            sess("D", "waiting")]


def base_s():
    return [sess("E", "busy", project=GAMMA)]


def build(em=None, s=None, config=None, em_age=30, em_projects=None, s_projects=None, now=NOW,
          em_live_kw=None):
    em = base_em() if em is None else em
    s = base_s() if s is None else s
    lives = [live("s-host", s, 10, s_projects), live("em-host", em, em_age, em_projects,
                                                    **(em_live_kw or {}))]
    return board.build([cache("s-host"), cache("em-host")], lives,
                       cfg() if config is None else config, now)


def entry(b, host="em-host"):
    got = [e for e in b["brief"]["hosts"] if e["host"] == host]
    assert len(got) == 1, [e["host"] for e in b["brief"]["hosts"]]
    return got[0]


def keyset(b):
    return {tuple(k) for k in b["brief"]["keys"]}


def sid(x):
    return x.get("id") if "id" in x else x.get("session_id")


def find(b, group, i, host="em-host"):
    got = [x for x in b[group] if sid(x) == i and x.get("host") == host]
    assert len(got) == 1, (group, i, host)
    return got[0]


def assert_identities(e, b, host="em-host"):
    assert e["busy_all"] == e["folded_busy"] + e["errored_busy"] + e["unkeyed_busy"]
    assert e["idle_all"] == e["folded_idle"] + e["errored_idle"] + e["unkeyed_idle"]
    # second side of the identity: the column lists themselves
    assert e["busy_all"] == len([x for x in b["busy"] if x.get("host") == host])
    assert e["idle_all"] == len([x for x in b["idle"] if x.get("host") == host])


def counts(e):
    return {k: e[k] for k in COUNT_KEYS if k not in ("warned", "git_failed_projects")}


# ---------- acceptance 1: counts and identities ----------

BASE_COUNTS = dict(folded_busy=1, folded_idle=1, errored_busy=1, errored_idle=0, unkeyed_busy=0,
                   unkeyed_idle=0, busy_all=2, idle_all=1)


def test_base_counts_and_keys():
    b = build()
    e = entry(b)
    assert counts(e) == BASE_COUNTS
    assert keyset(b) == {("em-host", "A"), ("em-host", "C")}
    assert len(b["brief"]["keys"]) == 2
    assert_identities(e, b)


def test_errored_idle_variant():
    b = build(em=base_em() + [sess("F", "idle", state_error=True)])
    e = entry(b)
    assert counts(e) == dict(BASE_COUNTS, errored_idle=1, idle_all=2)
    assert keyset(b) == {("em-host", "A"), ("em-host", "C")}
    assert_identities(e, b)


def test_unkeyed_busy_variant():
    em = base_em() + [sess("F", "idle", state_error=True), sess(None, "busy", name="n-G")]
    b = build(em=em)
    e = entry(b)
    assert counts(e) == dict(BASE_COUNTS, errored_idle=1, idle_all=2, unkeyed_busy=1, busy_all=3)
    assert keyset(b) == {("em-host", "A"), ("em-host", "C")}
    assert_identities(e, b)


def test_session_without_id_key_is_unkeyed_not_folded():
    g = sess("G", "busy")
    del g["id"]
    b = build(em=base_em() + [g])
    e = entry(b)
    assert e["unkeyed_busy"] == 1 and e["busy_all"] == 3 and e["folded_busy"] == 1
    assert_identities(e, b)
    assert not any(board.folded(b, x) for x in b["busy"] if not x.get("id"))


def test_errored_without_id_counts_as_errored_not_unkeyed():
    b = build(em=base_em() + [sess(None, "busy", state_error=True)])
    e = entry(b)
    assert (e["errored_busy"], e["unkeyed_busy"], e["busy_all"]) == (2, 0, 3)
    assert_identities(e, b)


def test_sub_counts_as_busy_and_folds():
    b = build(em=base_em() + [sess("S", "sub", subagents_running=1)])
    e = entry(b)
    assert (e["folded_busy"], e["busy_all"]) == (2, 3)
    assert ("em-host", "S") in keyset(b)
    assert_identities(e, b)


def test_missing_state_error_counts_as_clean():
    em = base_em()
    del em[0]["state_error"]          # A: old cache without the field
    b = build(em=em)
    assert counts(entry(b)) == BASE_COUNTS
    assert ("em-host", "A") in keyset(b)


@pytest.mark.parametrize("val", ["true", 1, "yes"])
def test_truthy_non_bool_state_error_is_not_an_error(val):
    em = base_em()
    em[0]["state_error"] = val
    b = build(em=em)
    assert ("em-host", "A") in keyset(b)


def test_brief_hosts_entry_per_brief_host_only():
    b = build()
    assert [e["host"] for e in b["brief"]["hosts"]] == ["em-host"]
    b = build(config=cfg(em_brief=True, s_brief=True))
    order = [h["name"] for h in b["hosts"] if h["name"] in ("s-host", "em-host")]
    assert [e["host"] for e in b["brief"]["hosts"]] == order


def test_brief_holds_only_keys_and_counts():
    b = build()
    assert set(b["brief"]) == {"hosts", "keys"}
    json.dumps(b["brief"])
    for k in b["brief"]["keys"]:
        assert len(k) == 2 and all(isinstance(x, str) for x in k)
    for e in b["brief"]["hosts"]:
        assert set(COUNT_KEYS) | {"host"} <= set(e)
        for k, v in e.items():
            if k != "host":
                assert isinstance(v, int) and not isinstance(v, bool), (k, v)


def test_columns_keep_folded_sessions():
    b = build()
    assert {sid(x) for x in b["busy"] if x["host"] == "em-host"} == {"A", "B"}
    assert {sid(x) for x in b["idle"] if x["host"] == "em-host"} == {"C"}


# ---------- folded / session_error ----------

def test_folded_verdict_on_base_board():
    b = build()
    assert board.folded(b, find(b, "busy", "A")) is True
    assert board.folded(b, find(b, "idle", "C")) is True
    assert board.folded(b, find(b, "busy", "B")) is False
    assert board.folded(b, find(b, "waiting", "D")) is False
    assert board.folded(b, find(b, "busy", "E", host="s-host")) is False


def test_folded_false_without_brief_key():
    b = build()
    nb = dict(b)
    del nb["brief"]
    assert board.folded(nb, find(b, "busy", "A")) is False


def test_folded_looks_only_at_keys():
    s = {"host": "em-host", "id": "zz", "state": "waiting", "state_error": True}
    assert board.folded({"brief": {"hosts": [], "keys": [["em-host", "zz"]]}}, s) is True
    assert board.folded({"brief": {"hosts": [], "keys": [["s-host", "zz"]]}}, s) is False
    clean = {"host": "em-host", "id": "zz", "state": "busy", "state_error": False}
    assert board.folded({"brief": {"hosts": [], "keys": []}}, clean) is False


def test_session_error_both_sides():
    assert board.session_error({"state_error": True}) is True
    assert board.session_error({"state_error": False}) is False
    assert board.session_error({}) is False
    for v in ("true", 1, None, [1]):
        assert board.session_error({"state_error": v}) is False


def test_other_groups_on_brief_host_never_fold():
    em = base_em() + [sess("BG", "stale", kind="background", idle_since=NOW - 200000),
                      sess("X", "ended", live=False, status="idle",
                           segments=[["busy", NOW - 2 * H, NOW - H]])]
    b = build(em=em)
    assert keyset(b) == {("em-host", "A"), ("em-host", "C")}
    for g in ("waiting", "stale", "ended_today"):
        for x in b[g]:
            assert board.folded(b, x) is False, (g, sid(x))
    assert "X" in [sid(x) for x in b["ended_today"]]


def test_unknown_host_sessions_never_fold():
    b = build(em_live_kw={"source": "none", "errors": ["live agents: failed"]})
    assert keyset(b) == set()
    e = entry(b)
    assert e["busy_all"] == 0 and e["idle_all"] == 0
    for x in b.get("unknown", []):
        assert board.folded(b, x) is False


# ---------- ruling 3: one key shared with an unfoldable session ----------

def test_same_id_one_errored_folds_neither():
    em = base_em() + [sess("dup", "busy"), sess("dup", "idle", state_error=True)]
    b = build(em=em)
    assert ("em-host", "dup") not in keyset(b)
    for g in ("busy", "idle"):
        for x in b[g]:
            if sid(x) == "dup":
                assert board.folded(b, x) is False, g
    e = entry(b)
    assert counts(e) == dict(folded_busy=1, folded_idle=1, errored_busy=1, errored_idle=1,
                             unkeyed_busy=1, unkeyed_idle=0, busy_all=3, idle_all=2)
    assert_identities(e, b)


def test_same_id_with_waiting_twin_does_not_fold():
    em = base_em() + [sess("dup", "busy"), sess("dup", "waiting")]
    b = build(em=em)
    assert ("em-host", "dup") not in keyset(b)


def test_same_id_on_other_host_does_not_interfere():
    s = base_s() + [sess("A", "busy", state_error=True)]
    b = build(s=s, config=cfg(em_brief=True, s_brief=True))
    assert ("em-host", "A") in keyset(b)
    assert ("s-host", "A") not in keyset(b)
    assert counts(entry(b)) == BASE_COUNTS
    se = entry(b, "s-host")
    assert (se["folded_busy"], se["errored_busy"], se["busy_all"]) == (1, 1, 2)
    assert_identities(se, b, "s-host")


# ---------- acceptance 2: label and notes ----------

def test_base_label_and_notes():
    b = build()
    assert board.brief_label(entry(b)) == \
        "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1）"
    assert board.brief_note(b, "busy") == "（em-host 2）"
    assert board.brief_note(b, "idle") == "（em-host 1）"


def test_label_with_errored_idle_and_unkeyed_busy():
    em = base_em() + [sess("F", "idle", state_error=True), sess(None, "busy")]
    b = build(em=em)
    assert board.brief_label(entry(b)) == \
        "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 2 · 空闲 1：出错 2，无会话 id 1）"
    assert board.brief_note(b, "busy") == "（em-host 3）"
    assert board.brief_note(b, "idle") == "（em-host 2）"


def test_label_zero_items_are_left_out():
    b = build(em=[sess("A", "busy")])
    assert board.brief_label(entry(b)) == "em-host · 进行中 1"
    assert board.brief_note(b, "idle") == ""
    b = build(em=[sess("C", "idle")])
    assert board.brief_label(entry(b)) == "em-host · 空闲 1"
    assert board.brief_note(b, "busy") == ""
    b = build(em=[sess("A", "busy"), sess("F", "idle", state_error=True)])
    assert board.brief_label(entry(b)) == "em-host · 进行中 1（另有单独列出的 空闲 1：出错 1）"
    b = build(em=[sess("A", "busy"), sess(None, "busy")])
    assert board.brief_label(entry(b)) == \
        "em-host · 进行中 1（另有单独列出的 进行中 1：无会话 id 1）"


def test_warned_folded_session_stays_folded_and_label_says_so():
    em = base_em()
    em[0]["errors"] = ["TaskUpdate: unknown taskId T9"]
    b = build(em=em)
    assert ("em-host", "A") in keyset(b)
    e = entry(b)
    assert e["warned"] == 1
    assert board.brief_label(e) == \
        "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1） · 告警 1"


def test_errors_on_unfolded_session_are_not_warnings():
    b = build()                       # B carries errors but is not folded
    e = entry(b)
    assert e["warned"] == 0
    assert "告警" not in board.brief_label(e)


def git_fail(*paths):
    return {p: {"last_commit": None, "commits": None, "error": "git: boom"} for p in paths}


def test_git_failed_project_counted_once():
    b = build(em_projects=git_fail(ALPHA))      # A and C both in ALPHA, both folded
    assert find(b, "busy", "A")["git_failed"] is True
    assert find(b, "idle", "C")["git_failed"] is True
    e = entry(b)
    assert e["git_failed_projects"] == 1
    assert board.brief_label(e) == ("em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1）"
                                    " · git 读取失败 1 个项目")


def test_git_failed_two_projects_counted_two():
    em = base_em()
    em[2] = sess("C", "idle", project=GAMMA)
    b = build(em=em, em_projects=git_fail(ALPHA, GAMMA))
    assert entry(b)["git_failed_projects"] == 2


def test_git_failed_only_on_unfolded_session_is_quiet():
    b = build(em_projects=git_fail(BETA))       # only B (errored, shown on its own)
    assert find(b, "busy", "B")["git_failed"] is True
    e = entry(b)
    assert e["git_failed_projects"] == 0
    assert "git 读取失败" not in board.brief_label(e)


def test_warning_and_git_segments_in_spec_order():
    em = base_em()
    em[0]["errors"] = ["TaskUpdate: unknown taskId T9"]
    b = build(em=em, em_projects=git_fail(ALPHA))
    assert board.brief_label(entry(b)) == (
        "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1）"
        " · 告警 1 · git 读取失败 1 个项目")


def test_note_empty_without_brief_host():
    for c in (cfg(em_brief=None), cfg(em_brief=False)):
        b = build(config=c)
        assert board.brief_note(b, "busy") == ""
        assert board.brief_note(b, "idle") == ""


def test_note_lists_several_hosts_and_skips_zero_ones():
    b = build(config=cfg(em_brief=True, s_brief=True))
    order = [h["name"] for h in b["hosts"] if h["name"] in ("s-host", "em-host")]
    want = {"s-host": "s-host 1", "em-host": "em-host 2"}
    assert board.brief_note(b, "busy") == "（%s）" % " · ".join(want[h] for h in order)
    assert board.brief_note(b, "idle") == "（em-host 1）"      # s-host has no idle session


def test_note_counts_errored_sessions_too():
    b = build(em=[sess("B", "busy", state_error=True), sess("B2", "busy", state_error=True)])
    assert keyset(b) == set()
    assert board.brief_note(b, "busy") == "（em-host 2）"


# ---------- acceptance 3: nothing else changes ----------

UNCHANGED = ("summary", "waiting", "busy", "idle", "stale_host_sessions")


@pytest.mark.parametrize("em_age", [30, 400, 700])
def test_brief_leaves_columns_summary_and_statusline_alone(em_age):
    em = base_em() + [sess("F", "idle", state_error=True), sess(None, "busy")]
    on = build(em=em, em_age=em_age, config=cfg(em_brief=True, s_brief=True))
    off = build(em=em, em_age=em_age, config=cfg(em_brief=None))
    for k in UNCHANGED:
        assert on[k] == off[k], k
    assert board.statusline_doc(on, NOW) == board.statusline_doc(off, NOW)


def test_absent_and_false_brief_give_identical_boards():
    a = build(config=cfg(em_brief=None))
    f = build(config=cfg(em_brief=False, s_brief=False))
    assert a == f
    assert a["brief"]["hosts"] == []
    assert a["brief"]["keys"] == []
    assert all(h["brief"] is False for h in a["hosts"])


def test_hosts_entry_carries_brief_flag():
    b = build()
    by = {h["name"]: h for h in b["hosts"]}
    assert by["em-host"]["brief"] is True
    assert by["s-host"]["brief"] is False


def test_build_does_not_mutate_inputs_with_brief():
    em, s = base_em(), base_s()
    em0, s0 = copy.deepcopy(em), copy.deepcopy(s)
    b1 = build(em=em, s=s)
    b2 = build(em=em, s=s)
    assert em == em0 and s == s0
    assert b1 == b2


# whole names only: "cross-host" in a docstring is not the host alias "s-host"
NAME_RE = re.compile(rb"(?<![\w-])(?:em-host|s-host)(?![\w-])")


def test_host_name_pattern_both_sides():
    assert NAME_RE.search(b'cfg["name"] == "em-host"')
    assert NAME_RE.search(b"if host == 's-host':")
    assert not NAME_RE.search(b"cross-host project navigator")
    assert not NAME_RE.search(b"this-host")


def test_source_has_no_fictional_host_names():
    bad = []
    for d, _, files in os.walk(os.path.join(ROOT, "projdeck")):
        if "__pycache__" in d:
            continue
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                data = fh.read()
            if NAME_RE.search(data):
                bad.append(os.path.relpath(p, ROOT))
    assert bad == []


# ---------- acceptance 4: freshness ----------

def em_host_entry(b):
    return {h["name"]: h for h in b["hosts"]}["em-host"]


def test_brief_age_aging_reports_local_clock_time():
    b = build(em_age=400)
    assert keyset(b) == {("em-host", "A"), ("em-host", "C")}
    got = board.brief_age(em_host_entry(b), NOW)
    assert got["at"] == NOW - 400
    assert got["age"] == 400


def test_brief_age_at_is_now_minus_age_when_now_moves():
    h = em_host_entry(build(em_age=400))
    got = board.brief_age(h, NOW + 50)            # still aging (450 s)
    assert got["at"] == NOW - 400 and got["age"] == 450


def test_brief_age_ignores_remote_clock():
    b = build(em_age=400, em_live_kw={"collected_at": NOW + 900})
    assert board.brief_age(em_host_entry(b), NOW)["at"] == NOW - 400


def test_brief_age_fresh_is_none():
    assert board.brief_age(em_host_entry(build(em_age=100)), NOW) is None


def test_brief_age_unknown_host_is_none():
    b = board.build([], [], cfg(), NOW)
    assert board.brief_age(em_host_entry(b), NOW) is None


def test_brief_age_stale_reports_time_too():
    got = board.brief_age(em_host_entry(build(em_age=700)), NOW)
    assert got["at"] == NOW - 700


def test_stale_brief_host_folds_nothing():
    b = build(em_age=700)
    assert keyset(b) == set()
    stale_ids = {sid(x) for x in b["stale_host_sessions"] if x.get("host") == "em-host"}
    assert {"A", "C"} <= stale_ids
    for x in b["stale_host_sessions"]:
        assert board.folded(b, x) is False
    e = entry(b)
    assert e["busy_all"] == 0 and e["idle_all"] == 0
    assert_identities(e, b)
    assert board.brief_note(b, "busy") == "" and board.brief_note(b, "idle") == ""
    assert all(x["n"] == 0 and x["rows"] == [] for x in b["lanes"].get("brief", [])
               if x["host"] == "em-host")


# ---------- acceptance 5: lanes ----------

def row_key(r):
    return (r["host"], r["session_id"])


def brief_lanes(b, host="em-host"):
    got = [x for x in b["lanes"]["brief"] if x["host"] == host]
    assert len(got) == 1
    return got[0]


def test_main_and_brief_lanes_partition_by_the_same_verdict():
    b = build()
    keys = keyset(b)
    main = {row_key(r) for r in b["lanes"]["rows"]}
    assert main.isdisjoint(keys)
    assert main == {("em-host", "B"), ("em-host", "D"), ("s-host", "E")}
    bl = brief_lanes(b)
    assert set(bl) >= {"host", "rows", "n", "more"}
    assert {row_key(r) for r in bl["rows"]} <= keys
    assert {row_key(r) for r in bl["rows"]} == {("em-host", "A"), ("em-host", "C")}
    assert bl["n"] == 2 and bl["more"] == 0
    assert bl["more"] == bl["n"] - len(bl["rows"])


def test_no_brief_host_no_brief_lanes():
    b = build(config=cfg(em_brief=False))
    assert b["lanes"]["brief"] == []
    assert {row_key(r) for r in b["lanes"]["rows"]} == {
        ("em-host", k) for k in "ABCD"} | {("s-host", "E")}


def many_folded(n):
    return [sess("f%02d" % i, "busy", started_at=NOW - 3 * H + i) for i in range(n)]


def test_thirteen_folded_lanes_cap_at_twelve():
    b = build(em=many_folded(13))
    assert len(b["brief"]["keys"]) == 13
    bl = brief_lanes(b)
    assert len(bl["rows"]) == 12 and bl["n"] == 13 and bl["more"] == 1
    assert {row_key(r) for r in bl["rows"]} <= keyset(b)
    assert {row_key(r) for r in b["lanes"]["rows"]} == {("s-host", "E")}
    assert b["lanes"]["more"] == 0


def test_twelve_folded_lanes_no_more():
    bl = brief_lanes(build(em=many_folded(12)))
    assert (len(bl["rows"]), bl["n"], bl["more"]) == (12, 12, 0)


def test_main_more_counts_only_unfolded_lanes():
    s = [sess("w%02d" % i, "waiting", idle_since=NOW - 1000 - i, project=GAMMA)
         for i in range(12)]
    em = [sess("A", "busy"), sess("C", "idle")]
    on = build(em=em, s=s)
    assert len(on["lanes"]["rows"]) == 12 and on["lanes"]["more"] == 0
    off = build(em=em, s=s, config=cfg(em_brief=False))
    assert len(off["lanes"]["rows"]) == 12 and off["lanes"]["more"] == 2


def commit_proj(*paths):
    return {p: {"last_commit": {"ts": NOW - 1800, "subject": "c"}, "commits": [NOW - 1800]}
            for p in paths}


def test_commit_marks_on_both_sides_of_a_split_project():
    em = [sess("A", "busy"), sess("B", "busy", state_error=True)]      # both ALPHA
    b = build(em=em, em_projects=commit_proj(ALPHA))
    main = {r["session_id"]: r for r in b["lanes"]["rows"] if r["host"] == "em-host"}
    folded_rows = {r["session_id"]: r for r in brief_lanes(b)["rows"]}
    assert set(main) == {"B"} and set(folded_rows) == {"A"}
    assert main["B"]["commits"], "unfolded lane lost its commit mark"
    assert folded_rows["A"]["commits"], "folded lane lost its commit mark"


def test_commit_marks_deduped_within_one_side():
    # control: without brief one project shows its commits on a single lane only
    em = [sess("A", "busy"), sess("B", "busy", state_error=True)]
    off = build(em=em, em_projects=commit_proj(ALPHA), config=cfg(em_brief=False))
    marked = [r for r in off["lanes"]["rows"] if r["host"] == "em-host" and r["commits"]]
    assert len(marked) == 1
    # inside the brief block, two folded lanes of one project also carry one set of marks
    em = [sess("A", "busy"), sess("C", "idle")]
    on = build(em=em, em_projects=commit_proj(ALPHA))
    marked = [r for r in brief_lanes(on)["rows"] if r["commits"]]
    assert len(marked) == 1
