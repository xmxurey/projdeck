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
                 "size_kb": 0, "headless": False}


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


def test_collect_groups_sessions(tmp_path, make_repo, make_session):
    root = tmp_path / "home"
    repo = make_repo(root / "proj", {"a.py": "1"})
    write(os.path.join(repo, "debug.log"))
    claude = tmp_path / "claude"
    make_session(claude, repo + "/sub", "in-repo", [user(repo + "/sub", "hi")], mtime=100)
    make_session(claude, repo, "newer", [user(repo, "yo")], mtime=200)
    make_session(claude, "/gone/dir", "orphan", [user("/gone/dir", "old")])
    out = agent.collect({"host": "h1", "roots": [str(root)], "scan_depth": 2,
                         "claude_dir": str(claude)})
    assert out["schema"] == 1 and out["host"] == "h1"
    by_path = {p["path"]: p for p in out["projects"]}
    p = by_path[repo]
    assert [s["id"] for s in p["sessions"]] == ["newer", "in-repo"]
    assert p["suspicious"] == ["debug.log"]
    assert p["git"]["untracked"] == 1 and p["errors"] == []
    gone = by_path["/gone/dir"]
    assert gone["exists"] is False and gone["sessions"][0]["id"] == "orphan"


def test_collect_session_outside_roots_uses_repo_toplevel(tmp_path, make_repo, make_session):
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
