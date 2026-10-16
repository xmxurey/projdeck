import os
import time

from projdeck import agent
from tests.conftest import run_git, write


def test_parse_status_branch_ahead_dirty_untracked():
    out = "## main...origin/main [ahead 2, behind 1]\0 M a.py\0?? new file.txt\0R  b.py\0old_b.py\0"
    info, untracked = agent.parse_status(out)
    assert info == {"branch": "main", "dirty": 2, "untracked": 1, "ahead": 2, "no_upstream": False}
    assert untracked == ["new file.txt"]


def test_parse_status_no_upstream_and_no_commits():
    info, _ = agent.parse_status("## feature\0")
    assert info["branch"] == "feature" and info["no_upstream"] is True and info["ahead"] == 0
    info, _ = agent.parse_status("## No commits yet on main\0?? x\0")
    assert info["branch"] == "main" and info["untracked"] == 1


def test_git_info_real_repo(tmp_path, make_repo):
    repo = make_repo(tmp_path / "r", {"a.py": "1"})
    write(os.path.join(repo, "a.py"), "2")
    write(os.path.join(repo, "sub", "tmp_x.py"), "3")
    info, untracked = agent.git_info(repo)
    assert info["branch"] == "main"
    assert info["dirty"] == 1 and info["untracked"] == 1
    assert info["no_upstream"] is True
    assert info["last_commit_ts"] > time.time() - 60
    assert untracked == ["sub/tmp_x.py"]


def test_git_info_ignored_files_not_listed(tmp_path, make_repo):
    repo = make_repo(tmp_path / "r", {".gitignore": ".env\n"})
    write(os.path.join(repo, ".env"), "SECRET=1")
    _, untracked = agent.git_info(repo)
    assert untracked == []


def test_git_info_repo_without_commits(tmp_path, make_repo):
    repo = make_repo(tmp_path / "r", commit=False)
    info, untracked = agent.git_info(repo)
    assert info["last_commit_ts"] == 0 and untracked == ["README.md"]


def test_is_suspicious():
    p = agent.DEFAULT_PATTERNS
    assert agent.is_suspicious("test_foo.py", p)
    assert not agent.is_suspicious("tests/test_foo.py", p)
    assert agent.is_suspicious("docs/migration/crontab-backup.txt", p)
    assert agent.is_suspicious("ashare/logs/run.log", p)
    assert agent.is_suspicious("IMPLEMENTATION_SUMMARY.md", p)
    assert agent.is_suspicious("scratch/notes.txt", p)
    assert not agent.is_suspicious("src/main.py", p)
    assert not agent.is_suspicious("Test_Foo.PY", p)  # case-sensitive on every OS


def test_last_mtime_skips_heavy_dirs(tmp_path):
    write(tmp_path / "a.txt")
    os.utime(str(tmp_path / "a.txt"), (1000, 1000))
    write(tmp_path / "node_modules" / "x.js")  # fresh, must be ignored
    assert agent.last_mtime(str(tmp_path)) == 1000


def test_progress_prefers_progress_md(tmp_path, make_repo):
    repo = make_repo(tmp_path / "r", {"PROGRESS.md": "# P\n\nstep 1 done\n"})
    assert agent.progress(repo, True) == "# P\nstep 1 done"
    repo2 = make_repo(tmp_path / "r2")
    assert agent.progress(repo2, True) == "init"
    assert agent.progress(str(tmp_path), False) == ""


def test_discover_repos_and_plain_dirs(tmp_path, make_repo):
    make_repo(tmp_path / "a")
    make_repo(tmp_path / "group" / "b")
    (tmp_path / "plain").mkdir()
    (tmp_path / ".hidden").mkdir()
    make_repo(tmp_path / "a" / "nested")  # inside a repo: not descended
    found = agent.discover([str(tmp_path)], depth=2)
    assert found == sorted([str(tmp_path / "a"), str(tmp_path / "group" / "b"), str(tmp_path / "plain")])
    assert str(tmp_path / "plain") not in agent.discover([str(tmp_path)], 2, include_plain=False)
