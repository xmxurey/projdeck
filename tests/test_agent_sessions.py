import json
import os
import subprocess
import sys

from projdeck import agent
from tests.conftest import user, write


def test_title_priority_custom_over_ai_over_prompt(tmp_path, make_session):
    cwd = "/w/p"
    fp = make_session(tmp_path, cwd, "s1", [
        {"type": "attachment", "cwd": cwd, "entrypoint": "cli"},
        user(cwd, "<command-name>/clear</command-name>"),
        user(cwd, "  first   real\nprompt "),
        {"type": "ai-title", "aiTitle": "AI title", "sessionId": "s1"},
        "not json at all {",
        {"type": "custom-title", "customTitle": "My name", "sessionId": "s1"},
    ], mtime=5000)
    s = agent.parse_session(fp)
    assert s == {"id": "s1", "cwd": cwd, "title": "My name", "last_ts": 5000,
                 "size_kb": 0, "headless": False, "recent": ["first real prompt"],
                 "last_reply": "", "prompts": ["first real prompt"]}


def test_title_falls_back_to_first_prompt_and_headless(tmp_path, make_session):
    fp = make_session(tmp_path, "/w/p", "s2", [user("/w/p", "x" * 200, entry="sdk-cli")])
    s = agent.parse_session(fp)
    assert s["title"] == "x" * 80 and s["headless"] is True


def test_session_without_cwd_is_ignored(tmp_path, make_session):
    fp = make_session(tmp_path, "/w/p", "s3", [{"type": "queue-operation"}])
    assert agent.parse_session(fp) is None


def test_owner_longest_prefix():
    assert agent.owner("/a/b/c", ["/a", "/a/b", "/a/bc"]) == "/a/b"
    assert agent.owner("/a/bc", ["/a/b"]) is None


def test_collect_groups_sessions(tmp_path, make_repo, make_session, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "home"
    repo = make_repo(root / "proj", {"a.py": "1"})
    write(os.path.join(repo, "debug.log"))
    claude = tmp_path / "claude"
    make_session(claude, repo + "/sub", "in-repo", [user(repo + "/sub", "hi")], mtime=100)
    make_session(claude, repo, "newer", [user(repo, "yo")], mtime=200)
    gone_dir = str(tmp_path / "gone" / "dir")
    make_session(claude, gone_dir, "orphan", [user(gone_dir, "old")])
    out = agent.collect({"host": "h1", "roots": [str(root)], "scan_depth": 2,
                         "claude_dir": str(claude)})
    assert out["schema"] == 1 and out["host"] == "h1"
    by_path = {p["path"]: p for p in out["projects"]}
    p = by_path[repo]
    assert [s["id"] for s in p["sessions"]] == ["newer", "in-repo"]
    assert p["suspicious"] == ["debug.log"]
    assert p["git"]["untracked"] == 1 and p["errors"] == []
    gone = by_path[gone_dir]
    assert gone["exists"] is False and gone["sessions"][0]["id"] == "orphan"


def test_collect_session_outside_roots_uses_repo_toplevel(tmp_path, make_repo, make_session,
                                                          monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    other = make_repo(tmp_path / "elsewhere")
    claude = tmp_path / "claude"
    make_session(claude, other + "/deep", "s", [user(other + "/deep", "hi")])
    os.makedirs(other + "/deep")
    out = agent.collect({"roots": [str(tmp_path / "nothing")], "claude_dir": str(claude)})
    assert [p["path"] for p in out["projects"]] == [other]


def test_main_via_stdin_like_remote(tmp_path):
    src = os.path.join(os.path.dirname(agent.__file__), "agent.py")
    req = json.dumps({"action": "collect", "host": "x", "roots": [str(tmp_path)],
                      "claude_dir": str(tmp_path / "none")})
    with open(src, "rb") as f:
        r = subprocess.run([sys.executable, "-", req], stdin=f, stdout=subprocess.PIPE, cwd=str(tmp_path))
    assert r.returncode == 0
    assert json.loads(r.stdout)["host"] == "x"


def test_main_reports_errors_as_json(capsys):
    assert agent.main(["agent.py", json.dumps({"action": "nope"})]) == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_collect_orphans_not_absorbed_by_earlier_orphan(tmp_path, make_session, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / "h"
    home.mkdir()
    claude = tmp_path / "claude"
    make_session(claude, str(home), "s1", [user(str(home), "a")])
    make_session(claude, str(home) + "/gone", "s2", [user(str(home) + "/gone", "b")])
    out = agent.collect({"roots": [str(tmp_path / "nothing")], "claude_dir": str(claude)})
    by = {p["path"]: p for p in out["projects"]}
    assert sorted(by) == [str(home), str(home) + "/gone"]
    assert by[str(home) + "/gone"]["exists"] is False
    assert [s["id"] for s in by[str(home) + "/gone"]["sessions"]] == ["s2"]


def test_collect_drops_sessions_outside_home(tmp_path, make_repo, make_session, monkeypatch):
    home = tmp_path / "home"
    root = home / "dev"
    repo = make_repo(root / "proj")
    claude = tmp_path / "claude"
    make_session(claude, repo, "kept", [user(repo, "hi")])
    scratch = make_repo(tmp_path / "tmp" / "scratch")
    make_session(claude, scratch, "temp", [user(scratch, "x")])
    make_session(claude, "/tmp/gone", "temp2", [user("/tmp/gone", "y")])
    monkeypatch.setenv("HOME", str(home))
    out = agent.collect({"roots": [str(root)], "claude_dir": str(claude)})
    assert [p["path"] for p in out["projects"]] == [repo]
    assert [s["id"] for s in out["projects"][0]["sessions"]] == ["kept"]


def test_collect_keeps_sessions_in_configured_roots_outside_home(tmp_path, make_repo,
                                                               make_session, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = make_repo(tmp_path / "srv" / "proj")
    claude = tmp_path / "claude"
    make_session(claude, repo, "s", [user(repo, "hi")])
    out = agent.collect({"roots": [str(tmp_path / "srv")], "claude_dir": str(claude)})
    assert [s["id"] for s in out["projects"][0]["sessions"]] == ["s"]


def test_collect_reports_home_and_follows_symlinked_cwd(tmp_path, make_repo, make_session,
                                                        monkeypatch):
    home = tmp_path / "home"
    repo = make_repo(home / "daily" / "digest")
    os.symlink(repo, str(home / "digest"))
    claude = tmp_path / "claude"
    link = str(home / "digest")
    make_session(claude, link, "s", [user(link, "hi")])
    monkeypatch.setenv("HOME", str(home))
    out = agent.collect({"roots": [str(home)], "claude_dir": str(claude)})
    assert out["home"] == str(home)
    by = {p["path"]: p for p in out["projects"]}
    assert [s["id"] for s in by[repo]["sessions"]] == ["s"]


def test_discover_skips_home_folders(tmp_path, make_repo, monkeypatch):
    home = tmp_path / "home"
    for d in ("Library", "Documents", "Downloads"):
        make_repo(home / d / "repo")
    make_repo(home / "code")
    monkeypatch.setenv("HOME", str(home))
    assert agent.discover(["~"], 3, include_plain=True) == [str(home / "code")]


def test_scan_sessions_survives_listdir_error(tmp_path, make_session, monkeypatch):
    claude = tmp_path / "claude"
    make_session(claude, "/a", "ok", [user("/a", "x")])
    make_session(claude, "/b", "bad", [user("/b", "x")])
    real = os.listdir

    def fake(p):
        if p.endswith("-b"):
            raise PermissionError(p)
        return real(p)
    monkeypatch.setattr(os, "listdir", fake)
    assert [s["id"] for s in agent.scan_sessions(str(claude))] == ["ok"]


def test_suspicious_list_is_capped_but_counted(tmp_path, make_repo, monkeypatch):
    repo = make_repo(tmp_path / "proj", {"a.py": "1"})
    for i in range(5):
        write(os.path.join(repo, "r%d.log" % i))
    monkeypatch.setattr(agent, "SUSPICIOUS_CAP", 3)
    p = agent.build_project(repo, agent.DEFAULT_PATTERNS)
    assert len(p["suspicious"]) == 3 and p["suspicious_count"] == 5


def assistant(cwd, *blocks):
    return {"type": "assistant", "cwd": cwd, "message": {"role": "assistant", "content": list(blocks)}}


def test_recent_prompts_and_last_reply(tmp_path, make_session):
    cwd = "/w/p"
    tool_result = {"type": "user", "cwd": cwd, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
    fp = make_session(tmp_path, cwd, "s4", [
        user(cwd, "one"), assistant(cwd, {"type": "text", "text": "reply one"}),
        user(cwd, "two"), user(cwd, "three"),
        assistant(cwd, {"type": "text", "text": "done:  tests\npass"}),
        user(cwd, "four " + "y" * 300),
        assistant(cwd, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}),
        tool_result,
        user(cwd, "[Request interrupted by user]"),
        user(cwd, "<command-name>/compact</command-name>"),
    ])
    s = agent.parse_session(fp)
    assert s["recent"] == ["two", "three", "four " + "y" * 115]  # oldest first, 120 chars max
    assert s["last_reply"] == "done: tests pass"  # last assistant text, tool-only turns skipped


def test_collect_marks_live_tmux_sessions(tmp_path, make_repo, monkeypatch):
    root = tmp_path / "home"
    repo = make_repo(root / "proj", {"a.py": "1"})
    monkeypatch.setattr(agent, "tmux_sessions", lambda: [("proj-proj-ab12", repo),
                                                        ("proj-proj-ab12-s1", repo + "/sub")])
    out = agent.collect({"roots": [str(root)], "scan_depth": 2, "claude_dir": str(tmp_path / "c")})
    assert out["projects"][0]["live_tmux"] == ["proj-proj-ab12", "proj-proj-ab12-s1"]


def test_tmux_sessions_parses_and_filters(monkeypatch):
    class R:
        returncode = 0
        stdout = "proj-a-1234\t/w/a\nwork\t/w/b\n"
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: R())
    assert agent.tmux_sessions() == [("proj-a-1234", "/w/a")]

    def missing(*a, **k):
        raise FileNotFoundError("tmux")
    monkeypatch.setattr(agent.subprocess, "run", missing)
    assert agent.tmux_sessions() == []


def test_unknown_action_names_it(capsys):
    agent.main(["agent.py", json.dumps({"action": "nope"})])
    assert json.loads(capsys.readouterr().out)["error"] == "ValueError: unknown action 'nope'"


def test_prompts_collected_filtered_truncated_and_capped(tmp_path, make_session):
    cwd = "/w/p"
    tool_result = {"type": "user", "cwd": cwd, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
    records = [user(cwd, "<command-name>/clear</command-name>"), tool_result,
               user(cwd, "[Request interrupted by user]"), user(cwd, "fix  the\nnginx config"),
               user(cwd, "z" * 400)]
    records += [user(cwd, "p%d" % i) for i in range(250)]
    s = agent.parse_session(make_session(tmp_path, cwd, "s9", records))
    assert len(s["prompts"]) == 200 and s["prompts"][-1] == "p249" and s["prompts"][0] == "p50"
    s2 = agent.parse_session(make_session(tmp_path, cwd, "s8", records[:5]))
    assert s2["prompts"] == ["fix the nginx config", "z" * 300]
    assert s2["recent"] == ["fix the nginx config", "z" * 120]


def test_compact_summary_is_not_a_prompt(tmp_path, make_session):
    cwd = "/w/p"
    summary = dict(user(cwd, "This session is being continued from a previous conversation."),
                   isCompactSummary=True)
    s = agent.parse_session(make_session(tmp_path, cwd, "s7", [summary, user(cwd, "real one")]))
    assert s["prompts"] == ["real one"] and s["recent"] == ["real one"] and s["title"] == "real one"


REGISTRY = """# 本机项目登记

标签四选一：**Quant** · **Invest**

## Quant

| 项目 | 状态 |
|---|---|
| `quant/` | 在跑 |
| `games/sell-high/` | 搁置 |

## Invest

| 项目 | 状态 |
|---|---|
| `fin/` | 活跃 |
| 没有反引号 | x |
"""


def test_read_registry_maps_paths_to_tags(tmp_path):
    write(tmp_path / "PROJECTS.md", REGISTRY)
    tags, order, flags = agent.read_registry(str(tmp_path))
    h = str(tmp_path)
    assert tags == {h + "/quant": "Quant", h + "/games/sell-high": "Quant", h + "/fin": "Invest"}
    assert order == ["Quant", "Invest"] and flags == set()
    assert agent.read_registry(str(tmp_path / "none")) == ({}, [], set())


def test_collect_attaches_tags(tmp_path, make_repo, make_session, monkeypatch):
    home = tmp_path / "home"
    q = make_repo(home / "quant")
    other = make_repo(home / "other")
    write(home / "PROJECTS.md", REGISTRY)
    claude = tmp_path / "c"
    make_session(claude, other, "s", [user(other, "hi")])
    monkeypatch.setenv("HOME", str(home))
    out = agent.collect({"roots": [str(home)], "claude_dir": str(claude)})
    by = {p["path"]: p for p in out["projects"]}
    assert by[q]["tag"] == "Quant" and by[other]["tag"] is None
    assert out["tags"] == ["Quant", "Invest"]


def test_collect_drops_headless_scheduled_sessions(tmp_path, make_repo, make_session,
                                                   monkeypatch):
    home = tmp_path / "home"
    repo = make_repo(home / "daily" / "digest")
    cron_only = str(home / "digest")
    claude = tmp_path / "claude"
    make_session(claude, repo, "me", [user(repo, "hi")])
    make_session(claude, repo, "cron", [user(repo, "gen", entry="sdk-cli")])
    make_session(claude, cron_only, "cron2", [user(cron_only, "gen", entry="sdk-cli")])
    monkeypatch.setenv("HOME", str(home))
    out = agent.collect({"roots": [str(home / "daily")], "claude_dir": str(claude)})
    assert [p["path"] for p in out["projects"]] == [repo]
    assert [s["id"] for s in out["projects"][0]["sessions"]] == ["me"]


def test_collect_with_registry_lists_only_registered_or_worked_on(tmp_path, make_repo,
                                                                  make_session, monkeypatch):
    home = tmp_path / "home"
    q = make_repo(home / "quant")
    make_repo(home / "uploads")
    worked = make_repo(home / "scratchpad")
    write(home / "PROJECTS.md", REGISTRY)
    claude = tmp_path / "claude"
    make_session(claude, worked, "s", [user(worked, "hi")])
    monkeypatch.setenv("HOME", str(home))
    out = agent.collect({"roots": [str(home)], "claude_dir": str(claude)})
    assert sorted(p["path"] for p in out["projects"]) == [q, worked]


def test_collect_without_registry_lists_everything(tmp_path, make_repo, monkeypatch):
    home = tmp_path / "home"
    make_repo(home / "a")
    make_repo(home / "uploads")
    monkeypatch.setenv("HOME", str(home))
    out = agent.collect({"roots": [str(home)], "claude_dir": str(tmp_path / "c")})
    assert len(out["projects"]) == 2


def test_registry_flag_marks_project(tmp_path, make_repo, monkeypatch):
    home = tmp_path / "home"
    a = make_repo(home / "a")
    b = make_repo(home / "b")
    write(home / "PROJECTS.md", "## Quant\n\n| x | y |\n|---|---|\n| 🚩 `a/` | 1 |\n| `b/` | 2 |\n")
    monkeypatch.setenv("HOME", str(home))
    out = agent.collect({"roots": [str(home)], "claude_dir": str(tmp_path / "c")})
    by = {p["path"]: p for p in out["projects"]}
    assert by[a]["tag"] == by[b]["tag"] == "Quant"
    assert by[a]["flag"] is True and by[b]["flag"] is False
