"""A git read that failed is "git?", not "non-git" and not absent from the report."""
from projdeck import model, report, ui
from projdeck.ui import strip_ansi

NOW = 100 * 86400 + 12 * 3600
ERR = "git: git timed out after 5s"


def row(path="/home/u/quant", **kw):
    r = {"host": "s-host", "path": path, "exists": True, "local": False, "ssh": "s-host",
         "home": "/home/u", "tier": "active", "activity": NOW - 3600, "stale_since": None,
         "last_mtime": 0, "git": None, "suspicious": [], "progress": "", "errors": [],
         "sessions": [], "tag": None, "flag": False}
    r.update(kw)
    return r


def line(r):
    return strip_ansi(ui.project_line(r, NOW).split("\t", 2)[2])


def test_git_failed_needs_empty_git_and_a_git_error():
    assert model.git_failed({"git": None, "errors": [ERR]})
    assert not model.git_failed({"git": None, "errors": []})
    assert not model.git_failed({"git": None, "errors": ["progress: x"]})
    assert not model.git_failed({"git": {"branch": "m"}, "errors": [ERR]})
    assert not model.git_failed({})


def test_list_line_marks_failed_git_but_not_real_non_git():
    assert "git?" in line(row(errors=[ERR])) and "non-git" not in line(row(errors=[ERR]))
    assert "non-git" in line(row()) and "git?" not in line(row())


def test_uncollected_minimal_row_marks_neither():
    r = row(errors=[ERR], uncollected=True)
    assert "git?" not in line(r) and "non-git" not in line(r)


def test_preview_names_failure_not_non_git():
    out = strip_ansi(ui.preview_text(row(errors=[ERR]), NOW))
    assert "git 读取失败" in out and "timed out" in out and "not a git repo" not in out


def test_preview_of_empty_repo_says_no_commits_yet():
    g = {"branch": "main", "dirty": 0, "ahead": 0, "untracked": 0, "no_upstream": False,
         "last_commit_ts": 0}
    out = strip_ansi(ui.preview_text(row(git=g), NOW))
    assert "no commits yet" in out and "y ago" not in out


def test_report_lists_git_unreadable_section():
    md = report.render([row(errors=[ERR]), row("/home/u/plain")], [], NOW)
    assert "## git unreadable (1)" in md
    assert "`s-host:/home/u/quant` git: git timed out after 5s" in md
    assert "/home/u/plain` git" not in md


def test_report_live_age_counts_from_synced_at_and_never_goes_negative():
    host = {"name": "s-host", "local": True, "mux": "zellij"}
    lv = {"host": "s-host", "synced_at": NOW - 3600, "collected_at": NOW + 5000,
          "zellij_exited": []}
    md = report.render([], [], NOW, board={"summary": {"zellij_exited": []}}, lives=[lv],
                       cfg={"hosts": [host]})
    assert "s-host: live data 1h ago" in md


def test_preview_and_report_give_the_same_reason_text():
    r = row(errors=["progress: x", ERR, "git: second"])
    assert model.git_errors(r) == [ERR, "git: second"]
    reason = "; ".join(model.git_errors(r))
    assert reason in strip_ansi(ui.preview_text(r, NOW))
    assert reason in report.render([r], [], NOW)
