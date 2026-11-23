import json
import os
import re
import shutil
import statistics
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "contrib" / "statusline-waiting.sh"
ORANGE = "\x1b[38;2;217;89;38m"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def item(project, sid):
    return {"host": "s-host", "project": project, "name": "n", "session_id": sid, "since": 1}


def write_state(cache, waiting, age=5, raw=None):
    live = cache / "live"
    live.mkdir(parents=True, exist_ok=True)
    text = raw if raw is not None else json.dumps(
        {"updated": time.time() - age, "waiting": waiting})
    (live / "statusline.state").write_text(text)


def run(cache, sid="me", strict=False, path=None, unset_cache=False):
    env = {"PATH": path or os.environ["PATH"], "HOME": str(cache / "home")}
    if not unset_cache:
        env["PROJ_CACHE"] = str(cache)
    pre = "set -euo pipefail; " if strict else ""
    code = f'{pre}. "{SCRIPT}"; out=$(proj_waiting_segment "{sid}"); rc=$?; printf "%s" "$out"; exit $rc'
    return subprocess.run(["bash", "-c", code], env=env, capture_output=True, text=True)


def test_excludes_own_session_and_colors(tmp_path):
    write_state(tmp_path, [item("quant-poly", "other"), item("x", "me")])
    r = run(tmp_path)
    assert r.returncode == 0
    assert ANSI.sub("", r.stdout) == "◆ 等你 1 · quant-poly"
    assert ORANGE in r.stdout


def test_three_others_appends_plus_two(tmp_path):
    write_state(tmp_path, [item("a", "1"), item("b", "2"), item("c", "3")])
    out = ANSI.sub("", run(tmp_path).stdout)
    assert out == "◆ 等你 3 · a +2"


def test_only_own_sessions_prints_nothing(tmp_path):
    write_state(tmp_path, [item("a", "me")])
    r = run(tmp_path)
    assert r.returncode == 0 and r.stdout == ""


def test_stale_is_silent(tmp_path):
    write_state(tmp_path, [item("a", "1")], age=200)
    r = run(tmp_path)
    assert r.returncode == 0 and r.stdout == ""


def test_heartbeat_age_still_shown(tmp_path):
    write_state(tmp_path, [item("a", "1")], age=50)
    assert ANSI.sub("", run(tmp_path).stdout) == "◆ 等你 1 · a"


@pytest.mark.parametrize("age,shown", [(100, True), (140, False)])
def test_threshold_is_120_seconds_not_the_60_second_heartbeat(tmp_path, age, shown):
    # F9: 50 / 200 alone let a threshold anywhere in (50, 200) pass, 60 included; these two
    # sit 20 s either side of 120, so a 60 (or 180) threshold fails one of them
    write_state(tmp_path, [item("a", "1")], age=age)
    assert (ANSI.sub("", run(tmp_path).stdout) == "◆ 等你 1 · a") is shown


@pytest.mark.parametrize("case", ["ok", "missing", "broken", "nojq", "nocache"])
def test_strict_mode_never_breaks_caller(tmp_path, case):
    kw = {}
    if case == "missing":
        pass
    elif case == "broken":
        write_state(tmp_path, [], raw="{not json")
    else:
        write_state(tmp_path, [item("a", "1")])
    if case == "nojq":
        empty = tmp_path / "bin"
        empty.mkdir()
        for tool in ("bash",):
            os.symlink(shutil.which(tool), empty / tool)
        kw["path"] = str(empty)
    if case == "nocache":
        kw["unset_cache"] = True
    r = run(tmp_path, strict=True, **kw)
    assert r.returncode == 0
    if case in ("ok",):
        assert "等你 1" in r.stdout
    else:
        assert r.stdout == ""


def test_no_caller_side_effects_and_fast(tmp_path):
    write_state(tmp_path, [item("a", "1")])
    before = sorted(p.name for p in tmp_path.rglob("*"))
    code = (f'. "{SCRIPT}"; a=1; '
            'snap() { declare -p | grep -v "^declare -- \\(before\\|after\\|_\\)="; }; before=$(snap); '
            'proj_waiting_segment me >/dev/null; '
            'after=$(snap); '
            '[ "$before" = "$after" ] && echo same')
    r = subprocess.run(["bash", "-c", code],
                       env={"PATH": os.environ["PATH"], "PROJ_CACHE": str(tmp_path)},
                       capture_output=True, text=True)
    assert r.stdout.strip() == "same"
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
    times = []
    for _ in range(10):
        t = time.perf_counter()
        run(tmp_path)
        times.append(time.perf_counter() - t)
    assert statistics.median(times) < 0.2


def test_waiting_colour_is_the_terminal_boards_wait_colour():
    # the status line and the terminal board show the same state: one orange, not two
    from projdeck import ui
    assert ORANGE == "\x1b[38;2;%d;%d;%dm" % ui.WAIT_RGB
    assert "\\u001b[38;2;%d;%d;%dm◆ 等你" % ui.WAIT_RGB in SCRIPT.read_text(encoding="utf-8")
