"""D7 board model: the one fold verdict (board.folded), per-host counts and their identities,
the header note / summary label, data age, and the swim-lane split (T08)."""
import copy
import json
import os
import re
import time

import pytest

from projdeck import board
from tests.test_board_sessions import live, sess

H = 3600
DAY = 1_800_000_000 // 86400 * 86400
NOW = DAY + 15 * H + 1200  # 15:20 UTC
COUNT_KEYS = ("folded_busy", "folded_idle", "errored_busy", "errored_idle",
              "unkeyed_busy", "unkeyed_idle", "busy_all", "idle_all", "warned",
              "git_failed_projects")


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def mkcfg(brief=True, extra=None):
    em = {"name": "em-host", "ssh": "em-host"}
    if brief is not None:
        em["brief"] = brief
    hosts = [{"name": "s-host", "local": True}, em]
    if extra:
        hosts.append(extra)
    return {"hosts": hosts}


def em_sessions():
    return [sess("A", "busy", started_at=NOW - 100),
            sess("B", "busy", started_at=NOW - 200, state_error=True),
            sess("C", "idle"),
            sess("D", "waiting")]


def build(em=None, s=None, cfg=None, em_at=NOW - 10, em_projects=None, extra_lives=()):
    em_live = live("em-host", em_sessions() if em is None else em, at=em_at)
    em_live["projects"] = em_projects or {}
    s_live = live("s-host", [sess("E", "busy")] if s is None else s, at=NOW - 5)
    return board.build([], [s_live, em_live] + list(extra_lives),
                       mkcfg() if cfg is None else cfg, NOW)


def entry(b, host="em-host"):
    [e] = [e for e in b["brief"]["hosts"] if e["host"] == host]
    return e


def keyset(b):
    return {tuple(k) for k in b["brief"]["keys"]}


def assert_identities(b):
    for e in b["brief"]["hosts"]:
        busy_n = sum(1 for s in b["busy"] if s["host"] == e["host"])
        idle_n = sum(1 for s in b["idle"] if s["host"] == e["host"])
        assert e["busy_all"] == e["folded_busy"] + e["errored_busy"] + e["unkeyed_busy"] == busy_n
        assert e["idle_all"] == e["folded_idle"] + e["errored_idle"] + e["unkeyed_idle"] == idle_n


# ---------------------------------------------------------------- 1. counts

def test_base_counts_and_keys():
    b = build()
    e = entry(b)
    got = {k: e[k] for k in COUNT_KEYS[:8]}
    assert got == {"folded_busy": 1, "folded_idle": 1, "errored_busy": 1, "errored_idle": 0,
                   "unkeyed_busy": 0, "unkeyed_idle": 0, "busy_all": 2, "idle_all": 1}
    assert keyset(b) == {("em-host", "A"), ("em-host", "C")}
    assert [x["host"] for x in b["brief"]["hosts"]] == ["em-host"]
    assert_identities(b)


VARIANTS = {
    "base": [],
    "errored_idle": [sess("F", "idle", state_error=True)],
    "unkeyed_busy": [sess("F", "idle", state_error=True), sess(None, "busy")],
}


@pytest.mark.parametrize("name", list(VARIANTS))
def test_identities_hold_in_every_variant(name):
    b = build(em=em_sessions() + VARIANTS[name])
    e = entry(b)
    assert_identities(b)
    if name != "base":
        assert e["errored_idle"] == 1 and e["idle_all"] == 2
    if name == "unkeyed_busy":
        assert e["unkeyed_busy"] == 1 and e["busy_all"] == 3
    assert keyset(b) == {("em-host", "A"), ("em-host", "C")}


def test_errored_session_without_id_counts_as_errored_not_unkeyed():
    b = build(em=[sess(None, "busy", state_error=True), sess("", "idle")])
    e = entry(b)
    assert (e["errored_busy"], e["unkeyed_busy"], e["unkeyed_idle"]) == (1, 0, 1)
    assert keyset(b) == set()
    assert_identities(b)


# ---------------------------------------------------------------- a key shared by sessions
# folded() reads the key only: a key one unfoldable session shares must not fold any of them

def test_shared_id_with_an_errored_session_folds_neither():
    b = build(em=[sess("A", "busy"), sess("A", "busy", state_error=True), sess("C", "idle")])
    same = [s for s in b["busy"] if s["id"] == "A"]
    assert len(same) == 2
    assert [board.folded(b, s) for s in same] == [False, False]
    assert keyset(b) == {("em-host", "C")}
    e = entry(b)
    assert (e["folded_busy"], e["errored_busy"], e["unkeyed_busy"]) == (0, 1, 1)
    assert_identities(b)
    assert [r["session_id"] for r in b["lanes"]["brief"][0]["rows"]] == ["C"]
    assert sum(r["session_id"] == "A" for r in b["lanes"]["rows"]) == 2


@pytest.mark.parametrize("other", [
    sess("A", "waiting"),
    sess("A", "ended", live=False, segments=[["busy", NOW - 2 * H, NOW - H]]),
    sess("A", "stale"),
])
def test_shared_id_with_a_session_outside_busy_idle_does_not_fold(other):
    b = build(em=[sess("A", "busy"), other, sess("C", "idle")])
    a = next(s for s in b["busy"] if s["id"] == "A")
    assert board.folded(b, a) is False
    assert keyset(b) == {("em-host", "C")}
    assert entry(b)["unkeyed_busy"] == 1
    assert_identities(b)


def test_two_foldable_sessions_sharing_an_id_both_fold_under_one_key():
    b = build(em=[sess("A", "busy"), sess("A", "idle")])
    assert b["brief"]["keys"] == [["em-host", "A"]]
    assert (entry(b)["folded_busy"], entry(b)["folded_idle"]) == (1, 1)
    assert_identities(b)


def test_same_id_on_another_host_does_not_block():
    b = build(s=[sess("A", "busy", state_error=True)])
    assert keyset(b) == {("em-host", "A"), ("em-host", "C")}


# ---------------------------------------------------------------- the verdict itself

def test_folded_is_true_only_for_keyed_sessions():
    b = build()
    by = {s["id"]: board.folded(b, s) for s in b["busy"] + b["idle"] + b["waiting"]}
    assert by == {"A": True, "B": False, "C": True, "D": False, "E": False}


def test_folded_false_without_brief_key_and_without_id():
    b = build()
    a = next(s for s in b["busy"] if s["id"] == "A")
    assert board.folded({k: v for k, v in b.items() if k != "brief"}, a) is False
    assert board.folded(b, dict(a, id=None)) is False
    assert board.folded(b, dict(a, host="s-host")) is False  # key is (host, id)


def test_folded_survives_a_json_round_trip():
    b = build()
    j = json.loads(json.dumps(b))
    assert [s["id"] for s in j["busy"] + j["idle"] if board.folded(j, s)] == ["A", "C"]


@pytest.mark.parametrize("val,want", [(True, True), (False, False), (None, False),
                                      ("true", False), (1, False)])
def test_session_error_is_exactly_true(val, want):
    assert board.session_error({"state_error": val}) is want


def test_session_error_missing_field_is_false():
    assert board.session_error({}) is False


def test_brief_holds_only_keys_and_counts():
    b = build()
    assert set(b["brief"]) == {"hosts", "keys"}
    assert set(entry(b)) == {"host"} | set(COUNT_KEYS)
    assert all(isinstance(entry(b)[k], int) for k in COUNT_KEYS)
    assert b["brief"]["keys"] == [["em-host", "A"], ["em-host", "C"]]


# ---------------------------------------------------------------- 2. label and note

def test_base_label_and_notes():
    b = build()
    assert board.brief_label(entry(b)) == "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1）"
    assert board.brief_note(b, "busy") == "（em-host 2）"
    assert board.brief_note(b, "idle") == "（em-host 1）"


def test_label_with_errored_idle_and_unkeyed_busy():
    b = build(em=em_sessions() + VARIANTS["unkeyed_busy"])
    assert board.brief_label(entry(b)) == (
        "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 2 · 空闲 1：出错 2，无会话 id 1）")


def test_label_without_separately_listed_sessions():
    b = build(em=[sess("A", "busy"), sess("C", "idle")])
    assert board.brief_label(entry(b)) == "em-host · 进行中 1 · 空闲 1"


def test_task_warning_still_folds_and_is_counted():
    ss = em_sessions()
    ss[0]["errors"] = ["TaskUpdate: unknown taskId 7"]
    b = build(em=ss)
    assert ("em-host", "A") in keyset(b)
    assert entry(b)["warned"] == 1
    assert board.brief_label(entry(b)).endswith("（另有单独列出的 进行中 1：出错 1） · 告警 1")


def test_git_failure_counted_once_per_project():
    b = build(em_projects={"/home/u/alpha": {"error": "git failed"}})
    folded = [s for s in b["busy"] + b["idle"] if board.folded(b, s)]
    assert [s["git_failed"] for s in folded] == [True, True]
    assert entry(b)["git_failed_projects"] == 1
    assert board.brief_label(entry(b)).endswith(" · git 读取失败 1 个项目")


def test_note_is_empty_without_brief_hosts():
    b = build(cfg=mkcfg(brief=None))
    assert board.brief_note(b, "busy") == board.brief_note(b, "idle") == ""


def test_note_lists_every_brief_host_with_sessions():
    x = live("x-host", [sess("X", "busy")], at=NOW - 10)
    z = {"name": "x-host", "ssh": "x-host", "brief": True}
    b = build(cfg=mkcfg(extra=z), extra_lives=[x])
    assert board.brief_note(b, "busy") == "（em-host 2 · x-host 1）"
    assert board.brief_note(b, "idle") == "（em-host 1）"  # x-host has no idle sessions


def test_note_counts_errored_hosts_too():
    b = build(em=[sess("B", "busy", state_error=True)])
    assert keyset(b) == set()
    assert board.brief_note(b, "busy") == "（em-host 1）"


# ---------------------------------------------------------------- 3. nothing else changes

def test_lists_and_statusline_unchanged_by_brief():
    on, off = build(), build(cfg=mkcfg(brief=None))
    for k in ("summary", "waiting", "busy", "idle", "stale_host_sessions"):
        assert on[k] == off[k], k
    assert board.statusline_doc(on, NOW) == board.statusline_doc(off, NOW)


def test_absent_brief_equals_brief_false():
    absent, false = build(cfg=mkcfg(brief=None)), build(cfg=mkcfg(brief=False))
    assert absent == false
    assert absent["brief"]["hosts"] == [] and absent["brief"]["keys"] == []
    assert absent["lanes"]["brief"] == []


def test_host_entries_carry_brief_flag():
    ghost = live("ghost", [], at=NOW - 10)  # only in the caches, not in the config
    b = build(extra_lives=[ghost])
    assert {h["name"]: h["brief"] for h in b["hosts"]} == {
        "s-host": False, "em-host": True, "ghost": False}


# a word of its own: "cross-host" in a docstring is not the host "s-host"
HOST_WORD = re.compile(r"(?<![\w-])(em-host|s-host)(?![\w-])")


def test_host_word_pattern_matches_names_only():
    assert HOST_WORD.search('name == "em-host"') and HOST_WORD.search("ssh s-host")
    assert not HOST_WORD.search("cross-host navigator")


def test_source_names_no_host():
    root = os.path.dirname(board.__file__)
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith((".py", ".css", ".js", ".html")):
                with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                    text = fh.read()
                assert not HOST_WORD.search(text), f


# ---------------------------------------------------------------- 4. data age

def em_host(b):
    return next(h for h in b["hosts"] if h["name"] == "em-host")


def test_brief_age_aging_is_measured_from_our_clock():
    b = build(em_at=NOW - 400)
    assert board.brief_age(em_host(b), NOW) == {"at": NOW - 400, "age": 400}
    assert keyset(b) == {("em-host", "A"), ("em-host", "C")}


def test_brief_age_fresh_is_none():
    assert board.brief_age(em_host(build(em_at=NOW - 100)), NOW) is None


def test_brief_age_uses_the_given_now():
    h = em_host(build(em_at=NOW - 100))
    assert board.brief_age(h, NOW + 300) == {"at": NOW - 100, "age": 400}


def test_brief_age_unknown_is_none():
    h = {"name": "em-host", "local": False, "live_unknown": True, "live_synced_at": None}
    assert board.brief_age(h, NOW) is None


def test_brief_host_age_looks_the_host_up_in_the_board():
    for at in (NOW - 400, NOW - 100, NOW - 700):
        b = build(em_at=at)
        assert board.brief_host_age(b, "em-host", NOW) == board.brief_age(em_host(b), NOW)
    assert board.brief_host_age(build(em_at=NOW - 400), "em-host", NOW)["age"] == 400


def test_brief_host_age_of_a_host_the_board_does_not_list_raises():
    # a made-up host entry has no live time, so it would read as fresh: refuse instead
    b = build(em_at=NOW - 400)
    b["hosts"] = [h for h in b["hosts"] if h["name"] != "em-host"]
    with pytest.raises(LookupError):
        board.brief_host_age(b, "em-host", NOW)


def test_brief_shown_follows_the_folded_keys():
    b = build()
    assert board.brief_shown(b, "em-host") is True
    b["brief"]["keys"] = [["em-host", "C"]]  # the idle one alone still makes a summary
    assert board.brief_shown(b, "em-host") is True
    b["brief"]["keys"] = [["s-host", "E"]]  # a key of another host does not count here
    assert board.brief_shown(b, "em-host") is False
    b["brief"]["keys"] = []
    assert board.brief_shown(b, "em-host") is False


def test_brief_shown_is_false_for_a_stale_host_and_for_a_host_with_only_errored_sessions():
    assert board.brief_shown(build(em_at=NOW - 700), "em-host") is False
    em = [sess("B", "busy", state_error=True), sess("D", "waiting")]
    assert board.brief_shown(build(em=em), "em-host") is False


def test_brief_label_leaves_zero_items_out():
    e = dict(entry(build()), folded_busy=0, folded_idle=2, errored_busy=0, errored_idle=0,
             unkeyed_busy=0, unkeyed_idle=0, warned=0, git_failed_projects=0)
    assert board.brief_label(e) == "em-host · 空闲 2"
    assert board.brief_label(dict(e, folded_idle=0)) == "em-host"


def test_stale_host_keeps_sessions_listed():
    b = build(em_at=NOW - 700)
    assert keyset(b) == set()
    stale_ids = {s["id"] for s in b["stale_host_sessions"] if s["host"] == "em-host"}
    assert {"A", "C"} <= stale_ids
    assert board.brief_age(em_host(b), NOW) == {"at": NOW - 700, "age": 700}
    assert b["lanes"]["brief"] == []


# ---------------------------------------------------------------- 5. lanes

def seg():
    return [["busy", NOW - 2 * H, NOW - H]]


def test_lanes_split_by_the_verdict():
    ss = [dict(s, segments=seg()) for s in em_sessions()]
    b = build(em=ss)
    main = {(r["host"], r["session_id"]) for r in b["lanes"]["rows"]}
    assert main.isdisjoint(keyset(b))
    assert {("em-host", "B"), ("em-host", "D"), ("s-host", "E")} <= main
    [blk] = b["lanes"]["brief"]
    assert blk["host"] == "em-host"
    got = {(r["host"], r["session_id"]) for r in blk["rows"]}
    assert got <= keyset(b) and got == {("em-host", "A"), ("em-host", "C")}
    assert blk["n"] == 2 and blk["more"] == blk["n"] - len(blk["rows"]) == 0
    assert (b["lanes"]["start"], b["lanes"]["end"]) == (NOW - 3 * H, NOW)


def test_brief_lanes_truncate_at_max_rows():
    ss = [sess("f%02d" % i, "busy", started_at=NOW - 100 - i) for i in range(13)]
    b = build(em=ss)
    [blk] = b["lanes"]["brief"]
    assert (len(blk["rows"]), blk["n"], blk["more"]) == (12, 13, 1)
    assert b["lanes"]["more"] == 0  # the folded ones do not overflow the main timeline


def test_commit_marks_on_both_sides_of_a_split_project():
    b = build(em=[sess("A", "busy"), sess("B", "busy", state_error=True)],
              em_projects={"/home/u/alpha": {"commits": [NOW - 600]}})
    main = [r for r in b["lanes"]["rows"] if r["host"] == "em-host"]
    [blk] = b["lanes"]["brief"]
    assert [r["session_id"] for r in main] == ["B"]
    assert main[0]["commits"] == [NOW - 600]
    assert blk["rows"][0]["commits"] == [NOW - 600]


def test_commit_marks_deduplicated_inside_a_brief_block():
    b = build(em=[sess("A", "busy"), sess("C", "idle")],
              em_projects={"/home/u/alpha": {"commits": [NOW - 600]}})
    [blk] = b["lanes"]["brief"]
    assert [r["commits"] for r in blk["rows"]] == [[NOW - 600], []]


def test_lanes_without_brief_unchanged():
    on = build(cfg=mkcfg(brief=None))
    raw = copy.deepcopy(on)
    raw.pop("brief")
    again = board.build_lanes(raw, {}, set(), NOW)
    assert again["rows"] == [dict(r, commits=[]) for r in on["lanes"]["rows"]]
    assert again["brief"] == []
