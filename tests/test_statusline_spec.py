import json
import os
import re
import shutil
import statistics
import subprocess
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "contrib", "statusline-waiting.sh")
BASH = shutil.which("bash")
REAL_JQ = shutil.which("jq")

pytestmark = pytest.mark.skipif(not (BASH and REAL_JQ), reason="needs bash and jq")

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SELF = "sess-self"


def item(project, sid, name="n"):
    return {"host": "s-host", "project": project, "name": name, "session_id": sid, "since": 1}


def write_state(cache, doc, raw=None):
    d = os.path.join(str(cache), "live")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "statusline.state"), "w") as f:
        f.write(raw if raw is not None else json.dumps(doc))


def doc(items, age=0):
    return {"updated": time.time() - age, "waiting": items}


def run_bash(body, cache, path=None, strict=False, unset_cache=False, extra_env=None):
    env = {"HOME": os.environ["HOME"], "PATH": path or os.environ["PATH"], "LANG": "C.UTF-8"}
    if cache is not None and not unset_cache:
        env["PROJ_CACHE"] = str(cache)
    env.update(extra_env or {})
    pre = "set -euo pipefail\n" if strict else ""
    script = pre + 'source "%s"\n' % SCRIPT + body + "\necho __END__ >&2\n"
    return subprocess.run([BASH, "-c", script], env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True)


def seg(cache, sid=SELF, **kw):
    r = run_bash('proj_waiting_segment "%s"' % sid, cache, **kw)
    return r


def plain(r):
    return ANSI.sub("", r.stdout).strip()


def test_two_items_one_self(tmp_path):
    write_state(tmp_path, doc([item("quant-poly", "other"), item("zzz", SELF)]))
    r = seg(tmp_path)
    assert r.returncode == 0
    assert plain(r) == "◆ 等你 1 · quant-poly"
    assert "240;163;94" in r.stdout


def test_self_first_shows_next_project(tmp_path):
    write_state(tmp_path, doc([item("mine", SELF), item("quant-poly", "other")]))
    assert plain(seg(tmp_path)) == "◆ 等你 1 · quant-poly"


def test_three_items_plus_two(tmp_path):
    write_state(tmp_path, doc([item("a", "1"), item("b", "2"), item("c", "3")]))
    out = plain(seg(tmp_path))
    assert out == "◆ 等你 3 · a +2"
    assert out.endswith(" +2")


def test_two_items_plus_one(tmp_path):
    write_state(tmp_path, doc([item("a", "1"), item("b", "2")]))
    assert plain(seg(tmp_path)) == "◆ 等你 2 · a +1"


def test_all_self_is_empty(tmp_path):
    write_state(tmp_path, doc([item("a", SELF), item("b", SELF)]))
    r = seg(tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_empty_waiting_is_empty(tmp_path):
    write_state(tmp_path, doc([]))
    r = seg(tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_missing_waiting_key_is_empty(tmp_path):
    write_state(tmp_path, {"updated": time.time()})
    r = seg(tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_stale_200s_is_empty(tmp_path):
    write_state(tmp_path, doc([item("a", "1")], age=200))
    r = seg(tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_heartbeat_50s_still_shows(tmp_path):
    write_state(tmp_path, doc([item("a", "1")], age=50))
    assert plain(seg(tmp_path)) == "◆ 等你 1 · a"


def test_missing_file_is_empty(tmp_path):
    r = seg(tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


@pytest.mark.parametrize("raw", ["{not json", "", "[", "null", "\x00\x01"])
def test_invalid_json_is_empty(tmp_path, raw):
    write_state(tmp_path, None, raw=raw)
    r = seg(tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == ""


def _nojq_path(tmp_path):
    d = tmp_path / "nojq"
    d.mkdir()
    for name in os.listdir("/usr/bin"):
        if name == "jq":
            continue
        try:
            os.symlink("/usr/bin/" + name, str(d / name))
        except OSError:
            pass
    return str(d)


STRICT_CASES = ["ok", "missing", "broken", "nojq", "nocache"]


@pytest.mark.parametrize("case", STRICT_CASES)
def test_strict_mode_never_aborts(tmp_path, case):
    cache = tmp_path / "c"
    kw = {"strict": True}
    if case == "ok":
        write_state(cache, doc([item("a", "1")]))
    elif case == "missing":
        cache.mkdir()
    elif case == "broken":
        write_state(cache, None, raw="{oops")
    elif case == "nojq":
        write_state(cache, doc([item("a", "1")]))
        kw["path"] = _nojq_path(tmp_path)
    elif case == "nocache":
        kw["unset_cache"] = True
    body = 'proj_waiting_segment "%s" >/dev/null; rc=$?; echo "rc=$rc"; echo survived' % SELF
    r = run_bash(body, cache, **kw)
    assert r.returncode == 0, r.stderr
    assert "survived" in r.stdout and "rc=0" in r.stdout
    assert "__END__" in r.stderr


def test_strict_ok_output_via_substitution(tmp_path):
    write_state(tmp_path, doc([item("a", "1")]))
    r = run_bash('SEG=$(proj_waiting_segment "x"); printf "%s" "$SEG"', tmp_path, strict=True)
    assert r.returncode == 0
    assert plain(r) == "◆ 等你 1 · a"


def test_nojq_outputs_nothing(tmp_path):
    write_state(tmp_path, doc([item("a", "1")]))
    r = seg(tmp_path, path=_nojq_path(tmp_path))
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_unset_proj_cache_uses_home(tmp_path):
    home = tmp_path / "h"
    write_state(home / ".cache" / "proj", doc([item("homeproj", "1")]))
    r = run_bash('proj_waiting_segment "x"', None, extra_env={"HOME": str(home)})
    assert r.returncode == 0
    assert plain(r) == "◆ 等你 1 · homeproj"


def test_exactly_one_jq_call(tmp_path):
    bindir = tmp_path / "fake"
    bindir.mkdir()
    log = tmp_path / "jq.log"
    w = bindir / "jq"
    w.write_text('#!/bin/sh\necho call >> "%s"\nexec "%s" "$@"\n' % (log, REAL_JQ))
    w.chmod(0o755)
    cache = tmp_path / "c"
    write_state(cache, doc([item("a", "1"), item("b", "2")]))
    r = seg(cache, path=str(bindir) + os.pathsep + os.environ["PATH"])
    assert plain(r) == "◆ 等你 2 · a +1"
    assert log.read_text().count("call") == 1


def test_no_caller_state_change_and_no_writes(tmp_path):
    cache = tmp_path / "c"
    write_state(cache, doc([item("a", "1"), item("b", SELF)]))
    body = (
        'f() { declare -p | grep -v -E "^declare -[-a-zA-Z]* (BASH_|EPOCH|RANDOM|SECONDS|LINENO|_=|PIPESTATUS|BASHPID|SRANDOM)" ; declare -F; }\n'
        'f > "$PROJ_CACHE/../before.txt"\n'
        'proj_waiting_segment "%s" > "$PROJ_CACHE/../out.txt"\n'
        'f > "$PROJ_CACHE/../after.txt"\n' % SELF
    )
    files_before = sorted(
        (os.path.join(dp, n), os.stat(os.path.join(dp, n)).st_mtime_ns)
        for dp, _, ns in os.walk(str(cache)) for n in ns)
    dirs_before = sorted(dp for dp, _, _ in os.walk(str(cache)))
    home = os.environ["HOME"]
    home_before = sorted(os.walk(home))
    r = run_bash(body, cache)
    assert r.returncode == 0, r.stderr
    before = (tmp_path / "before.txt").read_text()
    after = (tmp_path / "after.txt").read_text()
    assert before == after
    files_after = sorted(
        (os.path.join(dp, n), os.stat(os.path.join(dp, n)).st_mtime_ns)
        for dp, _, ns in os.walk(str(cache)) for n in ns)
    assert files_before == files_after
    assert dirs_before == sorted(dp for dp, _, _ in os.walk(str(cache)))
    assert sorted(os.walk(home)) == home_before
    assert plain(type("R", (), {"stdout": (tmp_path / "out.txt").read_text()})) == "◆ 等你 1 · a"


def test_median_latency_under_200ms(tmp_path):
    cache = tmp_path / "c"
    write_state(cache, doc([item("a", "1"), item("b", "2"), item("c", "3")]))
    samples = []
    for _ in range(10):
        r = run_bash('s=$EPOCHREALTIME; proj_waiting_segment x >/dev/null; e=$EPOCHREALTIME; '
                     'echo "$s $e"', cache)
        assert r.returncode == 0
        s, e = r.stdout.split()
        samples.append(float(e) - float(s))
    assert statistics.median(samples) <= 0.2, samples
