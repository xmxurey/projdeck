"""Shared plumbing for the mutation meta-tests (tests/test_*_spec.py that copy the tree,
break one line of code in the copy and require that a named test case then fails).

They are slow (every one copies the tree and starts a child pytest), so they run only when
PROJ_MUTATION_TESTS=1; use `requires_mutation` as the module's pytestmark.
"""
import os
import shutil
import site
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDED = (".git", ".worktrees", "__pycache__", ".pytest_cache")

requires_mutation = pytest.mark.skipif(
    os.environ.get("PROJ_MUTATION_TESTS") != "1",
    reason="mutation meta-tests are slow; set PROJ_MUTATION_TESTS=1 to run them")


def copy_tree(dst):
    """Copy the repository to dst without .git, other worktrees and caches."""
    shutil.copytree(ROOT, str(dst), ignore=shutil.ignore_patterns(*EXCLUDED))
    return dst


def child_env(home):
    """Environment for the child pytest: HOME, cache and config inside `home` (a tmp dir),
    nothing live, no outer terminal multiplexer."""
    home = str(home)
    os.makedirs(home, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PROJ_LIVE_TESTS", "PROJ_MUTATION_TESTS", "PYTHONPATH", "ZELLIJ_SESSION_NAME",
                        "ZELLIJ", "TMUX")}
    env.update(HOME=home, PROJ_CACHE=os.path.join(home, "cache"),
               PROJ_CONFIG=os.path.join(home, "config.json"), PYTHONUSERBASE=site.USER_BASE)
    return env


def apply_mutation(tree, rel, old, new):
    """Replace `old` by `new` in tree/rel; `old` must occur exactly once."""
    path = os.path.join(str(tree), rel)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert text.count(old) == 1, "mutation target must match exactly once in %s, got %d" % (
        rel, text.count(old))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new))


def run_pytest(tree, nodes, home, extra=(), timeout=300):
    """Run the given test files / node ids in a child pytest inside `tree`."""
    cmd = [sys.executable, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider"] + list(extra) + list(nodes)
    return subprocess.run(cmd, cwd=str(tree), env=child_env(home), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, universal_newlines=True, timeout=timeout)


def failed_nodes(output):
    """Node ids listed as FAILED/ERROR in the short summary of a run_pytest output."""
    out = set()
    for line in output.splitlines():
        if line.startswith(("FAILED ", "ERROR ")):
            out.add(line.split(" ", 1)[1].split(" - ", 1)[0].strip())
    return out


def assert_caught(result, nodes, what):
    """The mutation must have made every node in `nodes` fail (exit code 1: tests failed, not
    a usage or import error). Other failures in the same run do not count."""
    survived = set(nodes) - failed_nodes(result.stdout)
    assert result.returncode == 1 and not survived, "%s: still passing: %s\n%s" % (
        what, sorted(survived), result.stdout[-1500:])
