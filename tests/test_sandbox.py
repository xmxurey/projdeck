"""scripts/sandbox.py: probes outside pytest get no real HOME, cache, config or programs."""
import os
import site
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = str(ROOT / "scripts" / "sandbox.py")

PROBE = r'''
echo "HOME=$HOME"
echo "CACHE=$PROJ_CACHE"
echo "CONFIG=$PROJ_CONFIG"
echo "BOX=$PROJ_SANDBOX"
echo "UB=$PYTHONUSERBASE"
echo "LIVE=${PROJ_LIVE_TESTS-unset}"
echo "CLAUDE=$(claude agents --json)"
ssh s-host true
touch "$HOME/probe"
exit 7
'''


def run(tmp_path, args, **env_over):
    outer = tmp_path / "outer-home"
    outer.mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(outer), PROJ_LIVE_TESTS="1",
               PROJ_CACHE=str(tmp_path / "outer-cache"),
               PROJ_CONFIG=str(tmp_path / "outer-config.json"), **env_over)
    p = subprocess.run([sys.executable, SANDBOX] + args, env=env, cwd=str(tmp_path),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    vals = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
    return p, vals, outer


def test_probe_sees_only_the_sandbox(tmp_path):
    p, v, outer = run(tmp_path, ["--", "sh", "-c", PROBE], PYTHONUSERBASE="/x/ub")
    assert p.returncode == 7, p.stderr  # the command's own exit code
    box = v["BOX"]
    for key, outer_value in (("HOME", str(outer)), ("CACHE", str(tmp_path / "outer-cache")),
                             ("CONFIG", str(tmp_path / "outer-config.json"))):
        assert v[key] != outer_value
        assert v[key].startswith(box + os.sep), (key, v[key], box)
    assert v["LIVE"] == "unset"
    assert v["UB"] == "/x/ub"
    assert v["CLAUDE"] == "[]"
    assert not (outer / "probe").exists()
    assert not os.path.exists(box)  # removed without --keep
    assert "sandbox: 2 stand-in call(s)" in p.stderr
    assert "claude agents --json" in p.stderr and "ssh s-host true" in p.stderr


def test_user_site_survives_the_moved_home(tmp_path):
    """The failure this exists for: HOME pointed at a temp dir, so a `pip --user` pytest
    vanished and a probe concluded there was no pytest."""
    base = {k: v for k, v in os.environ.items() if k != "PYTHONUSERBASE"}
    outer = tmp_path / "outer-home"
    outer.mkdir()
    # what Python itself makes of the caller's HOME: the user site the caller can see
    expect = subprocess.run(
        [sys.executable, "-c", "import site; print(site.getuserbase())"],
        env=dict(base, HOME=str(outer)), stdout=subprocess.PIPE, text=True).stdout.strip()
    p = subprocess.run([sys.executable, SANDBOX, "--", "sh", "-c",
                        'echo "UB=$PYTHONUSERBASE"; echo "HOME=$HOME"'],
                       env=dict(base, HOME=str(outer)), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, timeout=60)
    v = dict(line.split("=", 1) for line in p.stdout.splitlines())
    assert v["HOME"] != str(outer)
    assert v["UB"] == expect and expect.startswith(str(outer))
    # and with this run's own user site handed in, the user-installed pytest is found
    p = subprocess.run([sys.executable, SANDBOX, "--", sys.executable, "-m", "pytest",
                        "--version"], env=dict(base, PYTHONUSERBASE=site.USER_BASE),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    assert p.returncode == 0, p.stderr


def test_config_fixture_is_copied_in(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"hosts": [{"name": "s-host"}]}')
    p, v, _ = run(tmp_path, ["--config", str(fixture), "--keep", "--", "sh", "-c",
                             'echo "BOX=$PROJ_SANDBOX"; echo "CFG=$(cat "$PROJ_CONFIG")";'
                             'echo x >> "$PROJ_CONFIG"'])
    assert p.returncode == 0, p.stderr
    assert v["CFG"] == '{"hosts": [{"name": "s-host"}]}'
    assert fixture.read_text() == '{"hosts": [{"name": "s-host"}]}'  # a copy, not the file
    assert os.path.isdir(v["BOX"]) and "sandbox: kept" in p.stderr
    import shutil
    shutil.rmtree(v["BOX"])


def test_no_command_is_an_error(tmp_path):
    p, _, _ = run(tmp_path, [])
    assert p.returncode == 2 and "no command" in p.stderr
