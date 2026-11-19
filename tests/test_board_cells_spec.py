"""Spec-derived tests for board.cells / today_start / day_bins (T12)."""
import calendar
import json
import os
import time

import pytest

from projdeck import board

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "mockup_terminal.json")
MAP = {"b": "busy", "s": "sub", "w": "wait", "live": "wait_now", "n": None}


@pytest.fixture(scope="module")
def fx():
    with open(FIX) as f:
        return json.load(f)


def conv(segs):
    return [[MAP[k], s * 60, e * 60] for k, s, e in segs]


def exp(lst):
    return [MAP[k] for k in lst]


# ---------- acceptance 1: frozen fixture ----------

def test_qp_24(fx):
    now = fx["now"] * 60
    assert now - 10800 == (517 - 180) * 60
    got = board.cells(conv(fx["segs"]["qp"]), (517 - 180) * 60, 24, 450)
    assert got == exp(fx["expected"]["qp_24"])


def test_pc_24(fx):
    got = board.cells(conv(fx["segs"]["pc"]), (517 - 180) * 60, 24, 450)
    assert got == exp(fx["expected"]["pc_24"])


def test_pc_48(fx):
    got = board.cells(conv(fx["segs"]["pc"]), (517 - 360) * 60, 48, 450)
    assert got == exp(fx["expected"]["pc_48"])


# ---------- acceptance 2: priority / threshold / empty ----------

def one(segs):
    return board.cells(segs, 1000, 1, 450)


def test_empty_all_none():
    assert board.cells([], 0, 24, 450) == [None] * 24
    assert board.cells([], 0, 0, 450) == []


def test_tie_priority_order():
    # each type covers 100s of the same cell
    def seg(k, a):
        return [k, 1000 + a, 1000 + a + 100]
    kinds = ["wait", "wait_now", "sub", "busy"]
    for i in range(len(kinds)):
        present = kinds[i:]
        segs = [seg(k, j * 100) for j, k in enumerate(present)]
        assert one(segs) == [present[0]]
    # order of input must not matter
    segs = [seg("busy", 0), seg("sub", 100), seg("wait_now", 200), seg("wait", 300)]
    assert one(segs) == ["wait"]
    assert one(list(reversed(segs))) == ["wait"]


def test_longest_wins_over_priority():
    segs = [["wait", 1000, 1040], ["busy", 1040, 1200]]
    assert one(segs) == ["busy"]


def test_threshold_30s_strict():
    assert one([["busy", 1000, 1030]]) == [None]      # exactly 30 -> not over
    assert one([["busy", 1000, 1031]]) == ["busy"]
    assert one([["busy", 1000, 1010]]) == [None]


def test_threshold_per_type_not_total():
    # 20s of each of 3 types: none exceeds 30s
    segs = [["wait", 1000, 1020], ["sub", 1020, 1040], ["busy", 1040, 1060]]
    assert one(segs) == [None]


def test_small_high_priority_does_not_beat_big_low():
    segs = [["wait", 1000, 1031], ["busy", 1031, 1400]]
    assert one(segs) == ["busy"]


def test_same_type_segments_accumulate_in_cell():
    segs = [["sub", 1000, 1020], ["sub", 1100, 1120]]
    assert one(segs) == ["sub"]


def test_segments_outside_range_ignored_and_clipped():
    assert one([["busy", 0, 999], ["busy", 1450, 3000]]) == [None]
    # only 20s inside the cell [1000,1450)
    assert one([["busy", 980, 1020]]) == [None]
    assert one([["busy", 1000 - 1000, 1000 + 40]]) == ["busy"]
    # spanning many cells
    got = board.cells([["sub", 0, 10 ** 6]], 1000, 3, 450)
    assert got == ["sub"] * 3


def test_cell_boundaries_step_and_start():
    segs = [["busy", 1000, 1450], ["wait", 1450, 1900]]
    assert board.cells(segs, 1000, 3, 450) == ["busy", "wait", None]
    assert board.cells(segs, 1450, 2, 450) == ["wait", None]


def test_returns_list_of_n():
    out = board.cells([["busy", 0, 5]], 0, 48, 450)
    assert isinstance(out, list) and len(out) == 48


def test_tuple_segments_accepted():
    assert one([("busy", 1000, 1100)]) == ["busy"]


def test_zero_length_segment_ignored():
    assert one([["busy", 1100, 1100]]) == [None]


# ---------- acceptance 3: timezone-dependent day functions ----------

@pytest.fixture
def tz(monkeypatch):
    def _set(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield _set
    monkeypatch.undo()
    time.tzset()


def local_ts(y, mo, d, h, mi, s=0):
    return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "UTC"])
def test_today_start_is_local_midnight(tz, zone):
    tz(zone)
    for hms in [(23, 59, 30), (0, 0, 30), (12, 0, 0), (0, 0, 0)]:
        now = int(local_ts(2026, 3, 10, *hms))
        ts = board.today_start(now)
        assert ts == int(local_ts(2026, 3, 10, 0, 0, 0))
        lt = time.localtime(ts)
        assert (lt.tm_hour, lt.tm_min, lt.tm_sec) == (0, 0, 0)


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "UTC"])
def test_day_bins_midnight_crossing_commit(tz, zone):
    tz(zone)
    commit = int(local_ts(2026, 3, 9, 23, 50))
    before = int(local_ts(2026, 3, 9, 23, 59, 30))
    after = int(local_ts(2026, 3, 10, 0, 0, 30))
    b1 = board.day_bins([commit], before)
    b2 = board.day_bins([commit], after)
    assert len(b1) == 14 and len(b2) == 14
    assert b1[-1] == 1 and sum(b1) == 1
    assert b2[-1] == 0 and b2[-2] == 1 and sum(b2) == 1


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "UTC"])
def test_day_bins_basic_shape(tz, zone):
    tz(zone)
    now = int(local_ts(2026, 3, 10, 15, 0))
    assert board.day_bins([], now) == [0] * 14
    ts = [int(local_ts(2026, 3, 10, 1, 0)), int(local_ts(2026, 3, 10, 14, 0)),
          int(local_ts(2026, 3, 8, 12, 0)), int(local_ts(2026, 3, 8, 12, 0)),
          int(local_ts(2026, 2, 25, 0, 0))]  # 13 days back -> first bin
    b = board.day_bins(ts, now)
    assert len(b) == 14
    assert b[-1] == 2
    assert b[-3] == 2
    assert b[0] == 1
    assert sum(b) == 5
    assert len(board.day_bins([], now, days=7)) == 7


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "UTC"])
def test_day_bins_out_of_window_dropped(tz, zone):
    tz(zone)
    now = int(local_ts(2026, 3, 10, 15, 0))
    old = int(local_ts(2026, 2, 24, 23, 59))  # 14 days back: outside
    assert sum(board.day_bins([old], now)) == 0
    first = int(local_ts(2026, 2, 25, 0, 0))
    assert board.day_bins([first], now)[0] == 1


def test_timezones_give_different_and_correct_results(tz):
    # same absolute instant, different local day
    instant = calendar.timegm((2026, 3, 10, 20, 0, 0))  # UTC 03-10 20:00 / Shanghai 03-11 04:00
    tz("UTC")
    utc_start = board.today_start(instant)
    assert utc_start == calendar.timegm((2026, 3, 10, 0, 0, 0))
    tz("Asia/Shanghai")
    sh_start = board.today_start(instant)
    assert sh_start == calendar.timegm((2026, 3, 11, 0, 0, 0)) - 8 * 3600
    assert sh_start != utc_start

    commit = calendar.timegm((2026, 3, 10, 10, 0, 0))  # UTC today; Shanghai 18:00 yesterday
    tz("UTC")
    assert board.day_bins([commit], instant)[-1] == 1
    tz("Asia/Shanghai")
    b = board.day_bins([commit], instant)
    assert b[-1] == 0 and b[-2] == 1
