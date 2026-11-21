"""conftest's hang watchdog: a stuck test ends the run with a stack dump instead of hanging.

A tester once sat 21 hours on a pytest that never finished and printed nothing, and a hung
pytest ignored SIGTERM. Each case runs a child pytest that loads tests/conftest.py as a plugin.
"""
import os
import site
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def child(tmp_path, body, timeout):
    (tmp_path / "test_child.py").write_text(body)
    # HOME is a tmp dir here (conftest): keep the user site so a `pip --user` pytest is found
    env = dict(os.environ, PROJ_TEST_TIMEOUT=str(timeout), PYTHONUSERBASE=site.USER_BASE,
               PYTHONPATH=os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]))
    start = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "-p", "tests.conftest", "--rootdir", str(tmp_path), "test_child.py"],
                       cwd=str(tmp_path), env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, timeout=120)
    return p, time.monotonic() - start


def test_hung_test_ends_the_run_and_names_itself(tmp_path):
    p, took = child(tmp_path, "import time\n\ndef test_stuck_here():\n    time.sleep(60)\n", 1)
    assert p.returncode != 0
    assert took < 30
    assert "Timeout" in p.stderr
    assert "test_stuck_here" in p.stderr  # the stack points at the hung test


def test_quick_tests_are_left_alone(tmp_path):
    p, _ = child(tmp_path, "import time\n\ndef test_a():\n    time.sleep(0.6)\n\n"
                           "def test_b():\n    time.sleep(0.6)\n", 1)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Timeout" not in p.stderr


def test_zero_turns_it_off(tmp_path):
    p, _ = child(tmp_path, "import time\n\ndef test_slow():\n    time.sleep(1.5)\n", 0)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Timeout" not in p.stderr
