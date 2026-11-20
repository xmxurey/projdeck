"""P1-11: the failure paths that used to be untested: ssh timeout, and the skipped lines that
`proj clean` / `proj restore` print. Nothing here reaches a real host: ssh is a stand-in."""
import os
import subprocess

import pytest

from projdeck import cli, sync, ui

SSH_HOST = {"name": "s-host", "ssh": "s-host"}


def test_run_agent_reports_timeout_text(monkeypatch):
    def fake_run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(sync.AgentError) as e:
        sync.run_agent(SSH_HOST, {"action": "collect"}, timeout=7)
    assert str(e.value) == "timed out after 7s"


def test_run_agent_really_times_out_on_a_hanging_ssh(tmp_path, monkeypatch):
    stub = tmp_path / "ssh"
    stub.write_text("#!/bin/sh\nexec sleep 30\n")  # exec: no grandchild keeps the pipes open
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    with pytest.raises(sync.AgentError) as e:
        sync.run_agent(SSH_HOST, {"action": "collect"}, timeout=0.2)
    assert str(e.value) == "timed out after 0.2s"


def test_clean_prints_each_skipped_file(monkeypatch, capsys):
    calls = []

    def fake_agent(host, req, timeout=20):
        calls.append(req["action"])
        if req["action"] == "list":
            return {"path": "/home/u/p", "files": [{"path": "a.log", "suspicious": True}]}
        return {"moved": ["a.log"], "skipped": ["gone.tmp", "other.log"], "batch": "B1"}
    monkeypatch.setattr(sync, "run_agent", fake_agent)
    monkeypatch.setattr(ui, "pick_files", lambda files, preview=None: ["a.log"])
    monkeypatch.setattr(cli, "_ask", lambda prompt: "y")
    monkeypatch.setattr(cli, "log_event", lambda *a, **k: None)
    cfg = {"hosts": [SSH_HOST], "trash_dir": "/tmp/t", "ssh_timeout": 20,
           "suspicious_patterns": []}
    cli.clean(cfg, "s-host", "/home/u/p")
    lines = capsys.readouterr().out.splitlines()
    assert calls == ["list", "trash"]
    assert "  skipped (no longer untracked or unsafe): gone.tmp" in lines
    assert "  skipped (no longer untracked or unsafe): other.log" in lines


def test_clean_skipped_names_are_sanitised(monkeypatch, capsys):
    """A skipped name comes from the remote agent: control sequences must not reach the terminal."""
    def fake_agent(host, req, timeout=20):
        if req["action"] == "list":
            return {"path": "/home/u/p", "files": [{"path": "a.log", "suspicious": True}]}
        return {"moved": [], "skipped": ["x\x1b[31mred\x07\nnext"], "batch": "B1"}
    monkeypatch.setattr(sync, "run_agent", fake_agent)
    monkeypatch.setattr(ui, "pick_files", lambda files, preview=None: ["a.log"])
    monkeypatch.setattr(cli, "_ask", lambda prompt: "y")
    monkeypatch.setattr(cli, "log_event", lambda *a, **k: None)
    cfg = {"hosts": [SSH_HOST], "trash_dir": "/tmp/t", "ssh_timeout": 20, "suspicious_patterns": []}
    cli.clean(cfg, "s-host", "/home/u/p")
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    assert "  skipped (no longer untracked or unsafe): xred next" in out.splitlines()


def test_restore_prints_each_skipped_file(monkeypatch, capsys):
    def fake_agent(host, req, timeout=20):
        if req["action"] == "batches":
            return {"batches": [{"id": "B1", "created": 1_800_000_000, "count": 2,
                                 "project": "p"}]}
        return {"restored": ["a.log"], "skipped": ["exists.log", "lost.log"]}
    monkeypatch.setattr(sync, "run_agent", fake_agent)
    monkeypatch.setattr(ui, "run_fzf", lambda lines, args, expect=(): ("", [lines[0]]))
    monkeypatch.setattr(cli, "log_event", lambda *a, **k: None)
    cfg = {"hosts": [SSH_HOST], "trash_dir": "/tmp/t", "ssh_timeout": 20}
    assert cli.cmd_restore(cfg, type("A", (), {"host": None})()) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "restored 1, skipped 2" in lines
    assert "  skipped (path exists or trash copy missing): exists.log" in lines
    assert "  skipped (path exists or trash copy missing): lost.log" in lines
