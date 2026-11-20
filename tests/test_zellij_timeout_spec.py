"""Discrimination checks for the zellij-timeout test: mutate a copy of the repo and
require that test_zellij_timeout_runs_claude_inline notices. Runs only in a tmp copy, and only with PROJ_MUTATION_TESTS=1 (see tests/_mutation.py)."""
import pytest

from tests import _mutation as M

pytestmark = M.requires_mutation

TARGET = "tests/test_zellij_create_spec.py::test_zellij_timeout_runs_claude_inline"

SLOW_OLD = '''    except (OSError, subprocess.SubprocessError):
        return None
    out, err = r.stdout or "", r.stderr or ""
    sessions = []'''
SLOW_NEW = '''    except (OSError, subprocess.SubprocessError):
        import time as _t
        _t.sleep(4)
        return None
    out, err = r.stdout or "", r.stderr or ""
    sessions = []'''
FALLBACK_OLD = '''    if sessions is None:
        return launch(r, r["path"])
    name = opener.proj_session_name('''
FALLBACK_NEW = '''    if sessions is None:
        sessions = []
    name = opener.proj_session_name('''


def _run_target(repo, tmp_path):
    return M.run_pytest(repo, [TARGET], tmp_path / "home", timeout=120)


def test_unmutated_copy_passes(tmp_path):
    r = _run_target(M.copy_tree(tmp_path / "repo"), tmp_path)
    assert r.returncode == 0, r.stdout[-2000:]


def test_slow_zellij_timeout_makes_the_case_fail(tmp_path):
    repo = M.copy_tree(tmp_path / "repo")
    M.apply_mutation(repo, "projdeck/agent.py", SLOW_OLD, SLOW_NEW)
    M.assert_caught(_run_target(repo, tmp_path), [TARGET], "slow zellij timeout")


def test_removed_inline_fallback_makes_the_case_fail(tmp_path):
    repo = M.copy_tree(tmp_path / "repo")
    M.apply_mutation(repo, "projdeck/cli.py", FALLBACK_OLD, FALLBACK_NEW)
    M.assert_caught(_run_target(repo, tmp_path), [TARGET], "removed inline fallback")
