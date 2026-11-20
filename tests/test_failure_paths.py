"""P1-9: failure paths of clean / restore / trash and the new-tab open give the right
conclusion. Hosts are stand-ins; nothing reaches a real host, zellij or claude."""
import json
import os
import subprocess

import pytest

from projdeck import agent, cli, config, opener, sync, ui

NOW = 1_800_000_000
H1 = {"name": "s-host", "ssh": "s-host"}
H2 = {"name": "em-host", "ssh": "em-host"}
CFG = {"hosts": [H1, H2], "trash_dir": "/tmp/t", "ssh_timeout": 20,
       "tiers_days": {"active": 14, "stale": 90}, "serve": {"port": 8787}}
BATCH = {"id": "B1", "created": NOW - 90 * 86400, "count": 2, "project": "/home/u/p"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("PROJ_CONFIG", str(tmp_path / "conf.json"))
    (tmp_path / "conf.json").write_text(json.dumps({"hosts": [H1, H2]}))


def agent_where(monkeypatch, behaviour):
    """behaviour: host name -> list of batches, or an exception to raise for `batches`;
    purge also fails for hosts mapped to an exception."""
    calls = []

    def fake(host, req, timeout=20):
        calls.append((host["name"], req["action"]))
        b = behaviour[host["name"]]
        if isinstance(b, Exception):
            raise b
        if req["action"] == "batches":
            return {"batches": b}
        if req["action"] == "purge":
            return {"purged": req["ids"]}
        return {"restored": [], "skipped": []}
    monkeypatch.setattr(sync, "run_agent", fake)
    return calls


def args(**kw):
    return type("A", (), dict({"host": None, "purge": False, "older": "30d"}, **kw))()


def test_all_batches_returns_pairs_and_failures(monkeypatch, capsys):
    agent_where(monkeypatch, {"s-host": sync.AgentError("down"), "em-host": [BATCH]})
    pairs, failed = cli._all_batches(CFG, None)
    assert [(h["name"], b["id"]) for h, b in pairs] == [("em-host", "B1")]
    assert [(n, str(e)) for n, e in failed] == [("s-host", "down")]
    assert "s-host: down" in capsys.readouterr().err


def test_restore_when_every_host_fails_does_not_say_trash_is_empty(monkeypatch, capsys):
    agent_where(monkeypatch, {"s-host": sync.AgentError("down"),
                              "em-host": sync.AgentError("timed out after 20s")})
    assert cli.cmd_restore(CFG, args()) == 1
    cap = capsys.readouterr()
    assert "trash is empty" not in cap.out + cap.err
    assert "s-host" in cap.err and "em-host" in cap.err


def test_restore_with_partial_failure_warns_and_still_picks(monkeypatch, capsys):
    agent_where(monkeypatch, {"s-host": sync.AgentError("down"), "em-host": [BATCH]})
    monkeypatch.setattr(ui, "pick_line", lambda lines, header, **k: lines[0])
    assert cli.cmd_restore(CFG, args()) == 0
    cap = capsys.readouterr()
    assert "list incomplete: 1 host(s)" in cap.err and "restored 0, skipped 0" in cap.out


def test_restore_with_truly_empty_trash_still_says_so(monkeypatch, capsys):
    agent_where(monkeypatch, {"s-host": [], "em-host": []})
    assert cli.cmd_restore(CFG, args()) == 0
    assert "trash is empty" in capsys.readouterr().out


def test_trash_lists_the_readable_host_and_exits_1(monkeypatch, capsys):
    agent_where(monkeypatch, {"s-host": sync.AgentError("down"), "em-host": [BATCH]})
    assert cli.cmd_trash(CFG, args()) == 1
    cap = capsys.readouterr()
    assert "em-host  B1" in cap.out and "s-host" in cap.err


def test_trash_without_failures_exits_0(monkeypatch, capsys):
    agent_where(monkeypatch, {"s-host": [], "em-host": [BATCH]})
    assert cli.cmd_trash(CFG, args()) == 0


def test_purge_with_nothing_old_names_failed_hosts_and_exits_1(monkeypatch, capsys):
    agent_where(monkeypatch, {"s-host": sync.AgentError("down"),
                              "em-host": [dict(BATCH, created=cli.time.time())]})
    assert cli.cmd_trash(CFG, args(purge=True)) == 1
    out = capsys.readouterr().out
    assert "no older batches on the hosts that could be read; 1 host(s) failed" in out


def test_purge_failure_on_one_host_still_purges_the_other(monkeypatch, capsys):
    calls = []

    def fake(host, req, timeout=20):
        calls.append((host["name"], req["action"]))
        if req["action"] == "batches":
            return {"batches": [BATCH]}
        if host["name"] == "s-host":
            raise sync.AgentError("went away")
        return {"purged": req["ids"]}
    monkeypatch.setattr(sync, "run_agent", fake)
    monkeypatch.setattr(cli, "_ask", lambda prompt: "yes")
    assert cli.cmd_trash(CFG, args(purge=True)) == 1
    cap = capsys.readouterr()
    assert ("em-host", "purge") in calls
    assert "em-host: purged 1" in cap.out
    assert "s-host" in cap.err and "went away" in cap.err


# ---- ^X in the browser

def test_ctrl_x_clean_failure_returns_to_the_list(monkeypatch):
    proj = {"path": "/home/u/p", "exists": True, "git": None, "suspicious": [], "progress": "",
            "errors": [], "sessions": [], "last_mtime": NOW - 100}
    cache = {"host": "s-host", "projects": [proj]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: [cache])
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: [])
    monkeypatch.setattr(sync, "load_caches", lambda cfg: [cache])
    picks = iter([("ctrl-x", "s-host", "/home/u/p")])
    seen = []

    def pick(rows, now, notice=None, select=None, board=None):
        seen.append(notice)
        try:
            return next(picks)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", pick)

    def boom(cfg, host, path):
        raise sync.AgentError("ssh: connection refused")
    monkeypatch.setattr(cli, "clean", boom)
    cfg = dict(CFG, hosts=[H1], auto_sync_minutes=30, dashboard_url=None)
    assert cli.cmd_browse(cfg, None) == 0
    assert len(seen) == 2 and "clean failed" in seen[1]


# ---- new tab in zellij

def test_new_tab_failure_is_a_notice_and_is_not_logged(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "zellij").write_text("#!/bin/sh\n[ \"$2\" = new-tab ] && exit 1\nexit 0\n")
    (bin_ / "zellij").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "other")
    monkeypatch.setattr(agent, "zellij_by_project", lambda paths: {})
    r = {"host": "s-host", "path": str(tmp_path), "local": True, "mux": "zellij", "exists": True,
         "sessions": [], "home": str(tmp_path)}
    monkeypatch.setattr(cli.config, "zellij_in_scope", lambda row: True)
    outcome, msg = cli.open_project([r], r)
    assert outcome is None and "zellij new-tab failed" in msg
    usage = os.path.join(sync.cache_dir(), "usage.jsonl")
    assert not os.path.exists(usage) or open(usage).read() == ""


# ---- --older goes through config.positive_number (one judgement, F8)

@pytest.mark.parametrize("v", ["abc", "nan", "inf", "0d", "-1d"])
def test_older_rejects_what_positive_number_rejects(v):
    with pytest.raises(SystemExit, match="--older"):
        cli._days(v)


def test_older_accepts_what_positive_number_accepts():
    assert cli._days("30d") == 30 and cli._days("0.5") == 0.5 and config.positive_number(0.5)


# ---- clean then restore in a repo whose file name is not UTF-8

def test_clean_then_restore_non_utf8_name_is_byte_exact(tmp_path, monkeypatch, capsys, make_repo):
    repo = make_repo(tmp_path / "proj", {"keep.py": "k"})
    bad = b"junk\xff.log"
    with open(os.path.join(os.fsencode(repo), bad), "wb") as f:
        f.write(b"\x00\xfebinary")
    trash = str(tmp_path / "trash")
    cfg = {"hosts": [{"name": "s-host", "local": True}], "trash_dir": trash, "ssh_timeout": 20,
           "suspicious_patterns": ["*.log"]}
    # the real fzf is replaced: select every offered line / the first batch line
    monkeypatch.setattr(ui, "run_fzf", lambda lines, args, expect=(): ("", list(lines)))
    monkeypatch.setattr(cli, "_ask", lambda prompt: "y")
    shown = []
    real_pick = ui.pick_files
    monkeypatch.setattr(ui, "pick_files", lambda files, preview=None: shown.append(
        [f["path"] for f in files]) or real_pick(files, preview=preview))
    cli.clean(cfg, "s-host", repo)  # the list must not crash
    assert shown and os.fsdecode(bad) in shown[0]
    assert not os.path.lexists(os.path.join(os.fsencode(repo), bad))
    assert "\udcff" not in capsys.readouterr().out
    monkeypatch.setattr(ui, "run_fzf", lambda lines, args, expect=(): ("", [lines[0]]))
    assert cli.cmd_restore(cfg, args()) == 0
    with open(os.path.join(os.fsencode(repo), bad), "rb") as f:
        assert f.read() == b"\x00\xfebinary"
