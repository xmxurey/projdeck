"""Black-box mutation checks for T14: the failure-path tests must catch wrong implementations.
Slow: runs only with PROJ_MUTATION_TESTS=1 (see tests/_mutation.py)."""
import os
import subprocess

import pytest

from projdeck import sync
from tests import _mutation as M

FC = "tests/test_failure_coverage.py::"
TARGETS = ["tests/test_failure_coverage.py", "tests/test_backlog_spec.py"]

# name -> (file, old, new, the cases that must fail)
MUTATIONS = {
    "no_timeout_branch": ("projdeck/sync.py", (
        '    except subprocess.TimeoutExpired:\n'
        '        raise AgentError("timed out after %ss" % timeout)\n'), "",
        [FC + "test_run_agent_reports_timeout_text"]),
    "no_clean_skipped": ("projdeck/cli.py", (
        '    for s in res["skipped"]:\n'
        '        print("  skipped (no longer untracked or unsafe): %s" % termtext.one_line(s))\n'), "",
        [FC + "test_clean_prints_each_skipped_file"]),
    "no_restore_skipped": ("projdeck/cli.py", (
        '    for s in res["skipped"]:\n'
        '        print("  skipped (path exists or trash copy missing): %s" % termtext.one_line(s))\n'), "",
        [FC + "test_restore_prints_each_skipped_file"]),
    "backlog_non_utf8_none": ("projdeck/agent.py", (
        '    r = parse_backlog(text)\n    done = sum(1 for i in r["items"]'),
        '    if "\\ufffd" in text:\n        return None\n'
        '    r = parse_backlog(text)\n    done = sum(1 for i in r["items"]',
        ["tests/test_backlog_spec.py::test_read_backlog_non_utf8_does_not_crash"]),
}


def run_copy(tmp_path, mutation):
    tree = M.copy_tree(tmp_path / "copy")
    if mutation:
        rel, old, new, nodes = MUTATIONS[mutation]
        M.apply_mutation(tree, rel, old, new)
        return M.run_pytest(tree, nodes, tmp_path / "h")
    return M.run_pytest(tree, TARGETS, tmp_path / "h")


@M.requires_mutation
def test_unmutated_copy_passes(tmp_path):
    r = run_copy(tmp_path, None)
    assert r.returncode == 0, r.stdout[-2000:]


@M.requires_mutation
@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_mutation_is_caught(tmp_path, mutation):
    M.assert_caught(run_copy(tmp_path, mutation), MUTATIONS[mutation][3], mutation)


# ---- acceptance 1: exact timeout text, black-box on the public function ----

def test_run_agent_timeout_text_exact_with_hanging_ssh(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    ssh = d / "ssh"
    ssh.write_text("#!/bin/sh\nexec sleep 30\n")
    ssh.chmod(0o755)
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ["PATH"])
    with pytest.raises(sync.AgentError) as ei:
        sync.run_agent({"name": "s-host", "ssh": "s-host"}, {"action": "collect"}, timeout=1)
    assert str(ei.value) == "timed out after 1s"


def test_run_agent_timeout_text_is_7s(monkeypatch):
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(sync.AgentError) as ei:
        sync.run_agent({"name": "s-host", "ssh": "s-host"}, {"action": "collect"}, timeout=7)
    assert str(ei.value) == "timed out after 7s"
