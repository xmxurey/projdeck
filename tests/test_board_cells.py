import json
import os
import time
from pathlib import Path

import pytest

from projdeck import board

FIX = json.loads((Path(__file__).parent / "fixtures" / "mockup_terminal.json").read_text())
MAP = FIX["map"]


def _segs(name):
    return [[MAP[k], a * 60, b * 60] for k, a, b in FIX["segs"][name]]


def _expected(name):
    return [MAP[k] for k in FIX["expected"][name]]


def test_cells_qp_24():
    assert board.cells(_segs("qp"), (517 - 180) * 60, 24, 450) == _expected("qp_24")


def test_cells_pc_24():
    assert board.cells(_segs("pc"), (517 - 180) * 60, 24, 450) == _expected("pc_24")


def test_cells_pc_48():
    assert board.cells(_segs("pc"), (517 - 360) * 60, 48, 450) == _expected("pc_48")


def test_cells_empty_segments_all_none():
    assert board.cells([], 0, 5, 450) == [None] * 5


def test_cells_below_30s_is_none():
    assert board.cells([["busy", 0, 30]], 0, 1, 450) == [None]
    assert board.cells([["busy", 0, 31]], 0, 1, 450) == ["busy"]


@pytest.mark.parametrize("order", [
    ["wait", "wait_now", "sub", "busy"],
    ["busy", "sub", "wait_now", "wait"],
])
def test_cells_tie_prefers_wait_then_wait_now_sub_busy(order):
    def segs(kinds):
        return [[k, i * 100, i * 100 + 100] for i, k in enumerate(kinds)]
    assert board.cells(segs(order), 0, 1, 400) == ["wait"]
    assert board.cells(segs([k for k in order if k != "wait"]), 0, 1, 300) == ["wait_now"]
    assert board.cells(segs([k for k in order if k in ("sub", "busy")]), 0, 1, 200) == ["sub"]


def test_cells_longest_wins_over_priority():
    segs = [["wait", 0, 40], ["busy", 40, 400]]
    assert board.cells(segs, 0, 1, 400) == ["busy"]


@pytest.fixture
def tz():
    old = os.environ.get("TZ")
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _local(tzname, y, mo, d, h, mi, s=0):
    os.environ["TZ"] = tzname
    time.tzset()
    return int(time.mktime((y, mo, d, h, mi, s, 0, 0, -1)))


@pytest.mark.parametrize("tzname", ["Asia/Shanghai", "UTC"])
def test_today_start_and_day_bins(tz, tzname):
    d = _local(tzname, 2026, 11, 18, 23, 59, 30)
    d2 = _local(tzname, 2026, 11, 19, 0, 0, 30)
    commit = _local(tzname, 2026, 11, 18, 23, 50)
    assert board.today_start(d) == _local(tzname, 2026, 11, 18, 0, 0)
    assert board.today_start(d2) == _local(tzname, 2026, 11, 19, 0, 0)
    a = board.day_bins([commit], d)
    b = board.day_bins([commit], d2)
    assert len(a) == len(b) == 14
    assert a[-1] == 1 and sum(a) == 1
    assert b[-1] == 0 and b[-2] == 1 and sum(b) == 1


def test_day_bins_ignores_out_of_range_and_differs_by_tz(tz):
    now_utc = _local("UTC", 2026, 11, 18, 23, 59, 30)
    ts = _local("UTC", 2026, 11, 18, 10, 0)  # 18:00 Shanghai, but now is already Nov 19 there
    os.environ["TZ"] = "UTC"
    time.tzset()
    utc = board.day_bins([ts, 0, now_utc + 10 ** 6], now_utc)
    os.environ["TZ"] = "Asia/Shanghai"
    time.tzset()
    sh = board.day_bins([ts], now_utc)
    assert utc[-1] == 1 and sum(utc) == 1
    assert sh[-1] == 0 and sh[-2] == 1 and sum(sh) == 1
