"""Files read from scanned projects are untrusted: a symlink must not pull in a file
outside the project, a FIFO or device must not block or exhaust memory, and the
project's own git config must not get to run commands."""
import json
import os
import subprocess
import threading

import pytest

from projdeck import agent

SECRET = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n"


def _proj(tmp_path):
    p = tmp_path / "proj"
    (p / "docs").mkdir(parents=True)
    secret = tmp_path / "id_ed25519"
    secret.write_text(SECRET)
    return p, secret


def _no_secret(obj):
    return "PRIVATE KEY" not in json.dumps(obj)


def test_backlog_symlink_to_a_file_outside_the_project_is_not_read(tmp_path):
    p, secret = _proj(tmp_path)
    os.symlink(secret, p / "docs" / "backlog.md")
    b = agent.read_backlog(str(p))
    assert b is None and _no_secret(b)


def test_backlog_through_a_symlinked_directory_outside_the_project_is_not_read(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "backlog.md").write_text(SECRET)
    os.symlink(outside, p / "docs")
    assert agent.read_backlog(str(p)) is None


def test_backlog_symlink_inside_the_project_still_works(tmp_path):
    p, _ = _proj(tmp_path)
    (p / "BACKLOG.md").write_text("## A\n- one\n")
    os.symlink(p / "BACKLOG.md", p / "docs" / "backlog.md")
    b = agent.read_backlog(str(p))
    assert b and [i["title"] for i in b["items"]] == ["one"]


def test_backlog_on_a_fifo_or_device_returns_at_once(tmp_path):
    p, _ = _proj(tmp_path)
    os.mkfifo(p / "docs" / "backlog.md")
    out = []
    t = threading.Thread(target=lambda: out.append(agent.read_backlog(str(p))), daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive() and out == [None]
    os.remove(p / "docs" / "backlog.md")
    os.symlink("/dev/zero", p / "docs" / "backlog.md")
    assert agent.read_backlog(str(p)) is None


def test_backlog_read_is_capped(tmp_path):
    p, _ = _proj(tmp_path)
    big = "## A\n- first\n" + ("x" * 1023 + "\n") * (3 * 1024)  # ~3 MiB
    (p / "docs" / "backlog.md").write_text(big)
    b = agent.read_backlog(str(p))
    assert b and b["items"][0]["title"] == "first"
    assert agent.UNTRUSTED_READ_MAX < len(big)


def test_run_ledger_symlinked_outside_the_checkout_is_not_read(tmp_path):
    p, secret = _proj(tmp_path)
    runs = p / "docs" / "dev" / "runs"
    (runs / "r1").mkdir(parents=True)
    (runs / ".active").write_text("r1")
    leak = tmp_path / "state.json"
    leak.write_text(json.dumps({"tasks": [{"id": "T1", "title": SECRET, "status": "done"}]}))
    os.symlink(leak, runs / "r1" / "state.json")
    errors = []
    r = agent._run_ledger_tasks(str(p), errors)
    assert r is None and _no_secret(r) and errors and _no_secret(errors)


def test_sdd_plan_reference_outside_the_checkout_is_not_read(tmp_path):
    p, _ = _proj(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("### Task 1: leaked title\n")
    sdd = p / ".superpowers" / "sdd"
    sdd.mkdir(parents=True)
    (sdd / "progress.md").write_text("Plan: %s\n- [ ] local step\n" % plan)
    errors = []
    r = agent._sdd_tasks(str(p), 0, errors)
    assert "leaked title" not in json.dumps(r)
    assert any("outside" in e for e in errors)


def test_plan_files_symlinked_outside_the_checkout_are_skipped(tmp_path):
    p, _ = _proj(tmp_path)
    outside = tmp_path / "plan.md"
    outside.write_text("### Task 1: leaked title\n")
    d = p / "docs" / "superpowers" / "plans"
    d.mkdir(parents=True)
    os.symlink(outside, d / "x.md")
    assert agent._plan_file_tasks(str(p), 0) is None


def test_repo_config_cannot_run_an_fsmonitor_hook(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    marker = tmp_path / "ran"
    hook = tmp_path / "hook.sh"
    hook.write_text("#!/bin/sh\ntouch %s\nexit 1\n" % marker)
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)], check=True)
    (repo / "f").write_text("x")
    agent.git_status(str(repo))
    assert not marker.exists()


@pytest.mark.parametrize("args", [("status",), ("log", "-1")])
def test_git_helper_isolates_repo_config(monkeypatch, args):
    seen = []

    def fake_run(cmd, **kw):
        seen.append((cmd, kw.get("env") or {}))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    agent.git("/x", *args)
    cmd, env = seen[0]
    assert "core.fsmonitor=false" in cmd
    assert env.get("GIT_OPTIONAL_LOCKS") == "0"


def test_progress_md_symlink_outside_the_project_is_not_shown(tmp_path):
    p, secret = _proj(tmp_path)
    os.symlink(secret, p / "PROGRESS.md")
    assert "PRIVATE KEY" not in agent.progress(str(p), False)
    os.remove(p / "PROGRESS.md")
    os.symlink("/dev/zero", p / "PROGRESS.md")
    assert agent.progress(str(p), False) == ""
    os.remove(p / "PROGRESS.md")
    (p / "PROGRESS.md").write_text("line one\n\nline two\n")
    assert agent.progress(str(p), False) == "line one\nline two"
