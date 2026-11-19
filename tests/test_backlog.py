import hashlib
import os

from projdeck import agent
from tests.conftest import run_git, write

FIXTURE = """# Backlog

intro text

## Now

- **Fix login** first line
  - sub item should not count
  - another sub
- plain item without bold
3. **Numbered** thing
   continuation line ✅ landed

### Small heading item

body of heading item

## Later

- ~~**Struck out** old idea~~
- **Open one** still to do
"""


def test_parse_backlog_fixture():
    r = agent.parse_backlog(FIXTURE)
    items = r["items"]
    assert r["raw"] is None
    assert [i["title"] for i in items] == [
        "Fix login", "plain item without bold", "Numbered", "Small heading item",
        "Struck out", "Open one"]
    assert [i["section"] for i in items] == ["Now"] * 4 + ["Later"] * 2
    assert [i["done"] for i in items] == [False, False, True, False, True, False]
    assert "sub item should not count" in items[0]["text"]


def test_parse_backlog_prose_and_empty():
    r = agent.parse_backlog("just some prose\nno list here\n")
    assert r["items"] == [] and r["raw"] == "just some prose\nno list here\n"
    assert agent.parse_backlog("") == {"items": [], "raw": None}
    assert agent.parse_backlog("  \n\n")["raw"] is None


def test_parse_backlog_limits():
    text = "".join("- item %d\n" % i for i in range(350))
    assert len(agent.parse_backlog(text)["items"]) == 300
    big = "- **t**\n" + "x" * 5000 + "\n"
    assert len(agent.parse_backlog(big)["items"][0]["text"].encode()) <= 2048
    assert len(agent.parse_backlog("- **" + "t" * 500 + "**")["items"][0]["title"]) == 160
    assert len(agent.parse_backlog("z" * 100000)["raw"]) == 64 * 1024


def tree_hash(root):
    h = hashlib.sha256()
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            h.update(p.encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()


def test_read_backlog_and_collect(tmp_path, make_repo):
    repo = make_repo(tmp_path / "r", {"a.py": "1", "docs/backlog.md": FIXTURE})
    write(os.path.join(repo, "a.py"), "2")
    run_git(repo, "commit", "-qam", "second commit")
    plain = make_repo(tmp_path / "plain", {"a.py": "1"})
    b = agent.read_backlog(repo)
    assert b["path"] == "docs/backlog.md" and b["open"] == 4 and b["done"] == 2
    assert isinstance(b["mtime"], int) and b["raw"] is None
    assert agent.read_backlog(plain) is None
    before = tree_hash(repo)
    p = agent.build_project(repo, [])
    assert p["git"]["last_commit_subject"] == "second commit"
    assert len(p["git"]["commit_ts_14d"]) == 2
    assert p["backlog"]["open"] == 4
    assert agent.build_project(plain, [])["backlog"] is None
    assert tree_hash(repo) == before
    assert agent.SCHEMA == 1


def test_git_log_failure_is_a_git_error_not_no_commits(tmp_path, make_repo, monkeypatch):
    repo = make_repo(tmp_path / "r", {"a.py": "1"})
    real = agent.git

    def flaky(path, *args):
        if args and args[0] == "log":
            raise RuntimeError("git timed out after 5s")
        return real(path, *args)

    monkeypatch.setattr(agent, "git", flaky)
    p = agent.build_project(repo, [])
    assert p["git"] is None
    assert any(e.startswith("git:") for e in p["errors"])


def test_empty_repo_has_no_commits_and_no_error(tmp_path):
    repo = str(tmp_path / "empty")
    os.makedirs(repo)
    run_git(repo, "init", "-q", "-b", "main")
    p = agent.build_project(repo, [])
    assert p["errors"] == []
    g = p["git"]
    assert (g["last_commit_ts"], g["last_commit_subject"], g["commit_ts_14d"]) == (0, "", [])
