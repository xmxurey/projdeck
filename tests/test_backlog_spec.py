"""Independent tests for T09: backlog parsing (design 3.1.6) and git additions (3.1.7)."""
import hashlib
import os
import subprocess
import time

from projdeck import agent
from tests.conftest import GIT_ENV, run_git, write

FIXTURE = """# Backlog

Intro prose that is not an item.

## 最优先

- **Fix login** first thing
  - sub bullet should not be an item
  - another sub bullet
- **Old thing** ~~removed~~
- **Shipped** done earlier
  continuation line with ✅ inside
- plain item without bold
2. **Numbered two** details

## 以后

1. **First numbered** body
10. **Tenth numbered**
### Small heading item
- **After heading** x
- ✅ **Title has check**
- **Strike in continuation**
  ~~not title line~~
"""


def by_title(items):
    return {i["title"]: i for i in items}


def test_fixture_item_count_and_titles():
    items = agent.parse_backlog(FIXTURE)["items"]
    titles = [i["title"] for i in items]
    assert titles == ["Fix login", "Old thing", "Shipped", "plain item without bold",
                      "Numbered two", "First numbered", "Tenth numbered",
                      "Small heading item", "After heading", "Title has check",
                      "Strike in continuation"]
    assert len(items) == 11


def test_title_is_bold_segment():
    t = by_title(agent.parse_backlog(FIXTURE)["items"])
    assert "Fix login" in t and "Numbered two" in t and "First numbered" in t
    # the whole line is not the title when a bold segment exists
    assert not any(k.startswith("**") for k in t)


def test_title_without_bold_is_line_minus_marker():
    t = by_title(agent.parse_backlog("## S\n- plain words here\n3. numbered plain\n")["items"])
    assert set(t) == {"plain words here", "numbered plain"}


def test_first_bold_wins():
    it = agent.parse_backlog("- lead **one** and **two**\n")["items"]
    assert it[0]["title"] == "one"


def test_section_is_enclosing_h2():
    t = by_title(agent.parse_backlog(FIXTURE)["items"])
    assert t["Fix login"]["section"] == "最优先"
    assert t["plain item without bold"]["section"] == "最优先"
    assert t["Numbered two"]["section"] == "最优先"
    assert t["First numbered"]["section"] == "以后"
    assert t["After heading"]["section"] == "以后"


def test_h3_heading_is_an_item_in_enclosing_section():
    t = by_title(agent.parse_backlog(FIXTURE)["items"])
    h = t["Small heading item"]
    assert h["section"] == "以后"
    assert h["done"] is False


def test_item_before_any_h2_has_section_none():
    it = agent.parse_backlog("- **a**\n\n## S\n- **b**\n")["items"]
    assert it[0]["section"] is None
    assert it[1]["section"] == "S"


def test_h1_does_not_become_item_or_section():
    it = agent.parse_backlog("# Title\n\n## S\n- **a**\n# Another H1\n- **b**\n")["items"]
    assert [i["title"] for i in it] == ["a", "b"]
    assert it[1]["section"] == "S"  # lead ruling: H1 only ends items, does not reset section


def test_strikethrough_in_title_line_is_done():
    t = by_title(agent.parse_backlog(FIXTURE)["items"])
    assert t["Old thing"]["done"] is True


def test_check_in_continuation_is_done():
    t = by_title(agent.parse_backlog(FIXTURE)["items"])
    assert t["Shipped"]["done"] is True


def test_check_in_title_line_is_done():
    t = by_title(agent.parse_backlog(FIXTURE)["items"])
    assert t["Title has check"]["done"] is True


def test_open_items_not_done():
    t = by_title(agent.parse_backlog(FIXTURE)["items"])
    for k in ("Fix login", "plain item without bold", "Numbered two", "First numbered",
              "Tenth numbered", "After heading"):
        assert t[k]["done"] is False, k


def test_strikethrough_only_in_continuation_is_not_done():
    t = by_title(agent.parse_backlog(FIXTURE)["items"])
    assert t["Strike in continuation"]["done"] is False


def test_h3_with_check_is_done():
    it = agent.parse_backlog("## S\n### Finished ✅\n### Pending\n")["items"]
    assert [i["done"] for i in it] == [True, False]


def test_indented_sublist_not_independent_and_in_text():
    r = agent.parse_backlog(FIXTURE)
    t = by_title(r["items"])
    assert "sub bullet should not be an item" not in t
    assert "sub bullet should not be an item" in t["Fix login"]["text"]
    assert "another sub bullet" in t["Fix login"]["text"]


def test_text_stops_at_next_item():
    t = by_title(agent.parse_backlog(FIXTURE)["items"])
    assert "Old thing" not in t["Fix login"]["text"]
    assert "continuation line" in t["Shipped"]["text"]
    assert "continuation line" not in t["plain item without bold"]["text"]


def test_text_stops_at_heading():
    it = agent.parse_backlog("## S\n- **a** one\n## T\n- **b**\n")["items"]
    assert "## T" not in it[0]["text"] and "T" not in it[0]["text"].replace("a", "")


def test_item_keys():
    for i in agent.parse_backlog(FIXTURE)["items"]:
        assert set(("title", "done", "section", "text")) <= set(i)
        assert isinstance(i["done"], bool)


def test_star_marker_is_not_an_item():
    r = agent.parse_backlog("## S\n* **a**\n")
    assert r["items"] == []
    assert r["raw"] == "## S\n* **a**\n"


def test_indented_marker_alone_is_not_an_item():
    r = agent.parse_backlog("  - **a**\n")
    assert r["items"] == []


def test_parsed_fixture_has_no_raw():
    assert agent.parse_backlog(FIXTURE)["raw"] is None


def test_prose_only_gives_raw():
    text = "Just some thoughts.\n\nNo list here at all.\n"
    r = agent.parse_backlog(text)
    assert r["items"] == []
    assert r["raw"] == text


def test_empty_text_gives_no_raw():
    r = agent.parse_backlog("")
    assert r["items"] == [] and r["raw"] is None


def test_raw_truncated_to_64kb():
    text = "word " * 40000  # 200000 chars
    r = agent.parse_backlog(text)
    assert r["items"] == []
    assert r["raw"] and len(r["raw"].encode()) <= 64 * 1024
    assert text.startswith(r["raw"])


def test_max_300_items():
    text = "## S\n" + "".join("- **item %d**\n" % n for n in range(350))
    r = agent.parse_backlog(text)
    assert len(r["items"]) == 300
    assert r["items"][0]["title"] == "item 0"
    assert r["items"][299]["title"] == "item 299"
    assert r["raw"] is None


def test_exactly_300_items_all_kept():
    text = "".join("- **i%d**\n" % n for n in range(300))
    assert len(agent.parse_backlog(text)["items"]) == 300


def test_item_text_capped_at_2kb():
    body = "\n".join("  " + "y" * 100 for _ in range(100))  # ~10KB of continuation
    r = agent.parse_backlog("- **big**\n" + body + "\n- **small**\n")
    assert len(r["items"][0]["text"].encode()) <= 2048
    assert r["items"][0]["text"].startswith("- **big**") or "big" in r["items"][0]["text"]
    assert len(r["items"]) == 2


def test_title_capped_at_160():
    r = agent.parse_backlog("- **%s**\n- %s\n" % ("a" * 500, "b" * 500))
    for i in r["items"]:
        assert 0 < len(i["title"]) <= 160


def test_crlf_input_still_parses():
    r = agent.parse_backlog("## S\r\n- **a**\r\n- **b**\r\n")
    assert [i["title"] for i in r["items"]] == ["a", "b"]


# ---- read_backlog ----

def test_read_backlog_missing_is_none(tmp_path):
    assert agent.read_backlog(str(tmp_path)) is None


def test_read_backlog_fields(tmp_path):
    write(tmp_path / "docs" / "backlog.md", FIXTURE)
    os.utime(str(tmp_path / "docs" / "backlog.md"), (1234567, 1234567))
    b = agent.read_backlog(str(tmp_path))
    assert b["path"] == "docs/backlog.md"
    assert b["mtime"] == 1234567
    assert len(b["items"]) == 11
    assert b["open"] + b["done"] == len(b["items"])
    assert b["done"] == sum(1 for i in b["items"] if i["done"]) == 3
    assert b["raw"] is None


def test_read_backlog_empty_file(tmp_path):
    write(tmp_path / "docs" / "backlog.md", "")
    b = agent.read_backlog(str(tmp_path))
    assert b is not None
    assert b["items"] == [] and b["raw"] is None
    assert b["open"] == 0 and b["done"] == 0


def test_read_backlog_prose_raw(tmp_path):
    write(tmp_path / "docs" / "backlog.md", "free text only\n")
    b = agent.read_backlog(str(tmp_path))
    assert b["items"] == [] and b["raw"] == "free text only\n"


def test_read_backlog_non_utf8_does_not_crash(tmp_path):
    p = tmp_path / "docs"
    p.mkdir()
    (p / "backlog.md").write_bytes(b"- **ok**\n- bad \xff\xfe bytes\n")
    b = agent.read_backlog(str(tmp_path))
    assert b is None or len(b["items"]) >= 1


# ---- collect ----

def commit_at(path, msg, ts, fname):
    write(os.path.join(path, fname), msg)
    run_git(path, "add", "-A")
    env = dict(os.environ, **GIT_ENV, GIT_COMMITTER_DATE="%d +0000" % ts,
               GIT_AUTHOR_DATE="%d +0000" % ts)
    subprocess.run(["git", "-C", path, "commit", "-q", "-m", msg], check=True, env=env,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def collect_one(tmp_path, monkeypatch, proj):
    home = os.path.dirname(os.path.dirname(proj))
    monkeypatch.setenv("HOME", home)
    out = agent.collect({"roots": [home], "claude_dir": str(tmp_path / "c")})
    return out, {p["path"]: p for p in out["projects"]}[proj]


def test_collect_commit_subject_and_ts(tmp_path, make_repo, monkeypatch):
    proj = str(tmp_path / "home" / "dev" / "p")
    make_repo(proj, commit=False)
    now = int(time.time())
    commit_at(proj, "first one", now - 7200, "a.txt")
    commit_at(proj, "second: latest | subject", now - 3600, "b.txt")
    out, p = collect_one(tmp_path, monkeypatch, proj)
    assert p["git"]["last_commit_subject"] == "second: latest | subject"
    ts = p["git"]["commit_ts_14d"]
    assert sorted(ts) == [now - 7200, now - 3600]
    assert p["git"]["last_commit_ts"] == now - 3600
    assert out["schema"] == 1


def test_collect_commit_ts_excludes_old_commits(tmp_path, make_repo, monkeypatch):
    proj = str(tmp_path / "home" / "dev" / "p")
    make_repo(proj, commit=False)
    now = int(time.time())
    commit_at(proj, "ancient", now - 60 * 86400, "a.txt")
    commit_at(proj, "recent", now - 3600, "b.txt")
    _, p = collect_one(tmp_path, monkeypatch, proj)
    assert p["git"]["commit_ts_14d"] == [now - 3600]
    assert p["git"]["last_commit_subject"] == "recent"


def test_collect_commit_ts_capped_at_2000(tmp_path, make_repo, monkeypatch):
    proj = str(tmp_path / "home" / "dev" / "p")
    make_repo(proj, commit=False)
    write(os.path.join(proj, "a"), "a")
    run_git(proj, "add", "-A")
    now = int(time.time())
    # fast-import 2100 commits within the last day
    lines = []
    for n in range(2100):
        lines.append("commit refs/heads/main\ncommitter t <t@t> %d +0000\ndata 1\nm\n" % (now - 5000 + n))
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", "-C", proj, "fast-import", "--quiet"], input="".join(lines).encode(),
                   check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    run_git(proj, "reset", "-q", "--hard", "main")
    _, p = collect_one(tmp_path, monkeypatch, proj)
    assert len(p["git"]["commit_ts_14d"]) == 2000


def test_collect_no_backlog_is_none(tmp_path, make_repo, monkeypatch):
    proj = str(tmp_path / "home" / "dev" / "p")
    make_repo(proj)
    _, p = collect_one(tmp_path, monkeypatch, proj)
    assert p["backlog"] is None


def test_collect_backlog_present(tmp_path, make_repo, monkeypatch):
    proj = str(tmp_path / "home" / "dev" / "p")
    make_repo(proj, {"README.md": "hi", "docs/backlog.md": FIXTURE})
    _, p = collect_one(tmp_path, monkeypatch, proj)
    b = p["backlog"]
    assert b["path"] == "docs/backlog.md"
    assert len(b["items"]) == 11 and b["open"] + b["done"] == 11 and b["raw"] is None


def test_collect_is_read_only_on_project(tmp_path, make_repo, monkeypatch):
    proj = str(tmp_path / "home" / "dev" / "p")
    make_repo(proj, {"README.md": "hi", "docs/backlog.md": FIXTURE,
                     ".superpowers/x.md": "s"})

    def tree_hash():
        h = hashlib.sha256()
        for root, dirs, files in os.walk(proj):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            for f in sorted(files):
                fp = os.path.join(root, f)
                h.update(os.path.relpath(fp, proj).encode())
                with open(fp, "rb") as fh:
                    h.update(fh.read())
                h.update(str(os.stat(fp).st_mtime_ns).encode())
        return h.hexdigest()
    before = tree_hash()
    collect_one(tmp_path, monkeypatch, proj)
    assert tree_hash() == before
    assert not os.path.exists(os.path.join(os.environ["HOME"], ".claude"))


def test_schema_still_1():
    assert agent.SCHEMA == 1


# ---- review round 1: empty repo vs failing git log ----

def test_collect_empty_repo_has_zero_values_and_no_git_error(tmp_path, make_repo, monkeypatch):
    proj = str(tmp_path / "home" / "dev" / "p")
    make_repo(proj, commit=False)
    _, p = collect_one(tmp_path, monkeypatch, proj)
    assert p["git"] is not None
    assert p["git"]["last_commit_ts"] == 0
    assert p["git"]["last_commit_subject"] == ""
    assert p["git"]["commit_ts_14d"] == []
    assert not any(str(e).startswith("git:") for e in p["errors"])


def test_collect_git_log_failure_sets_git_none_and_error(tmp_path, make_repo, monkeypatch):
    import shutil
    proj = str(tmp_path / "home" / "dev" / "p")
    make_repo(proj)
    real = shutil.which("git")
    bindir = tmp_path / "fakegit"
    bindir.mkdir()
    script = bindir / "git"
    script.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do\n"
        "  if [ \"$a\" = log ]; then echo 'fatal: boom' >&2; exit 128; fi\n"
        "done\n"
        "exec %s \"$@\"\n" % real)
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    _, p = collect_one(tmp_path, monkeypatch, proj)
    assert p["git"] is None
    assert any(str(e).startswith("git:") for e in p["errors"])
