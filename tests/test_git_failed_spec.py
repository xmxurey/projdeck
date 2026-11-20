"""T07 spec tests: git read failure is shown as `git?`, not `non-git`, and is not dropped from report."""
from projdeck import board, model, report, ui

DAY = 86400
NOW = 1_800_000_000
CFG = {"hosts": [{"name": "s-host", "local": True}, {"name": "em-host", "ssh": "em"}],
       "tiers_days": {"active": 14, "stale": 90}}


def proj(path, git="ok", errors=(), **kw):
    g = {"branch": "main", "dirty": 0, "untracked": 0, "ahead": 0, "no_upstream": False,
         "last_commit_ts": NOW - 3600, "last_commit_subject": "s", "commit_ts_14d": [NOW - 3600]}
    p = {"path": path, "exists": True, "last_mtime": NOW - 100, "suspicious": [], "progress": "",
         "sessions": [], "errors": list(errors), "git": g if git == "ok" else None}
    p.update(kw)
    return p


def cache(host, projects, at=NOW - 100, **kw):
    d = {"schema": 1, "host": host, "collected_at": at, "synced_at": at, "stale_since": None,
         "last_error": None, "projects": list(projects)}
    d.update(kw)
    return d


FAIL = proj("/home/u/broken", git=None, errors=["git: git timed out after 5s"])
NOGIT = proj("/home/u/plain", git=None)
EMPTY = proj("/home/u/empty")
EMPTY["git"]["last_commit_ts"] = 0


def rows_of(*projects, caches=None):
    caches = caches or [cache("s-host", projects)]
    return model.rows(caches, CFG, NOW), caches


def row_for(path, *projects):
    rows, _ = rows_of(*projects)
    return [r for r in rows if r["path"] == path][0]


def display(path, *projects):
    rows, caches = rows_of(*projects)
    b = board.build(caches, [], CFG, NOW)
    lines = [ui.strip_ansi(x) for x in ui.project_lines(rows, NOW, b)]
    hit = [x for x in lines if path.rsplit("/", 1)[1] in x and "\t" in x]
    assert hit, lines
    return hit[0]


# ---- model.git_failed

def test_git_failed_true_for_git_error_and_no_git():
    assert model.git_failed({"git": None, "errors": ["git: boom"]}) is True


def test_git_failed_false_without_error_or_with_git_or_other_error():
    assert not model.git_failed({"git": None, "errors": []})
    assert not model.git_failed({"git": None})
    assert not model.git_failed({"git": None, "errors": ["progress: x"]})
    assert not model.git_failed({"git": {"branch": "m"}, "errors": ["git: boom"]})
    assert not model.git_failed({"git": None, "errors": ["not git: x"]})


def test_git_failed_matches_any_entry():
    assert model.git_failed({"git": None, "errors": ["other", "git: boom"]})


def test_board_private_helper_is_gone():
    assert not hasattr(board, "_collect_git_failed")


# ---- list line: three states

def test_failed_row_shows_git_question_not_non_git():
    d = display("/home/u/broken", FAIL)
    assert "git?" in d and "non-git" not in d


def test_real_non_git_dir_still_non_git():
    d = display("/home/u/plain", NOGIT)
    assert "non-git" in d and "git?" not in d


def test_healthy_repo_shows_neither():
    d = display("/home/u/empty", proj("/home/u/empty"))
    assert "non-git" not in d and "git?" not in d


def test_not_yet_collected_minimal_row_shows_neither():
    lv = {"schema": 1, "host": "s-host", "collected_at": NOW - 10, "source": "claude agents",
          "projects": {}, "errors": [], "sessions": [
              {"id": "w", "project": "/home/u/only-live", "kind": "interactive", "live": True,
               "name": "w", "title": "w", "started_at": NOW - 5000, "status": "idle",
               "state": "waiting", "idle_since": NOW - 1000, "question": "", "subagents_running": 0,
               "segments": [["busy", NOW - 3000, NOW - 2000]], "attach": "", "errors": []}]}
    b = board.build([], [lv], CFG, NOW)
    lines = [ui.strip_ansi(x) for x in ui.project_lines([], NOW, b)]
    hit = [x for x in lines if "only-live" in x]
    assert hit and "non-git" not in hit[0] and "git?" not in hit[0]


def test_git_error_on_one_project_does_not_flag_other_projects():
    d = display("/home/u/plain", FAIL, NOGIT)
    assert "git?" not in d and "non-git" in d


def test_git_error_in_one_project_does_not_make_host_unknown():
    b = board.build([cache("s-host", [FAIL, proj("/home/u/ok")])],
                    [{"schema": 1, "host": "s-host", "collected_at": NOW - 10, "source": "claude agents",
                      "sessions": [], "projects": {}, "errors": ["/home/u/broken: git: boom"]}], CFG, NOW)
    assert {h["name"]: h for h in b["hosts"]}["s-host"]["live_unknown"] is False


# ---- preview

def test_preview_of_failed_row_states_reason():
    t = ui.preview_text(row_for("/home/u/broken", FAIL), NOW)
    assert "git 读取失败" in t and "timed out" in t
    assert "not a git repo" not in t


def test_preview_of_real_non_git_keeps_old_text():
    t = ui.preview_text(row_for("/home/u/plain", NOGIT), NOW)
    assert "not a git repo" in t
    assert "git 读取失败" not in t


def test_preview_failure_reason_is_the_cached_error_text():
    p = proj("/home/u/broken", git=None, errors=["git: fatal: dubious ownership xyzzy"])
    t = ui.preview_text(row_for("/home/u/broken", p), NOW)
    assert "xyzzy" in t


def test_preview_ignores_non_git_errors_for_failure_text():
    p = proj("/home/u/plain", git=None, errors=["progress: unreadable"])
    t = ui.preview_text(row_for("/home/u/plain", p), NOW)
    assert "git 读取失败" not in t


# ---- empty repo

def test_empty_repo_preview_says_no_commits_yet():
    t = ui.preview_text(row_for("/home/u/empty", EMPTY), NOW)
    assert "no commits yet" in t
    assert "y ago" not in t


def test_repo_with_commit_does_not_say_no_commits_yet():
    t = ui.preview_text(row_for("/home/u/ok", proj("/home/u/ok")), NOW)
    assert "no commits yet" not in t


def test_empty_repo_line_not_56_years():
    d = display("/home/u/empty", EMPTY)
    assert "56y" not in d and "56" not in d.split("\t")[-1].replace("/home/u/empty", "")


# ---- report

def test_report_has_git_unreadable_section_with_host_path_and_error():
    rows, caches = rows_of(FAIL, proj("/home/u/ok"))
    md = report.render(rows, caches, NOW)
    assert "## git unreadable (1)" in md
    sect = md.split("## git unreadable (1)", 1)[1].split("\n## ", 1)[0]
    line = [x for x in sect.splitlines() if "s-host:/home/u/broken" in x]
    assert line and "timed out" in line[0]
    assert "/home/u/ok" not in sect


def test_report_without_failures_has_empty_section_like_others():
    rows, caches = rows_of(proj("/home/u/ok"), NOGIT)
    md = report.render(rows, caches, NOW)
    assert "## git unreadable (0)\n\n- none" in md or "## git unreadable (0)\n- none" in md
    assert "git unreadable (1)" not in md


def test_report_counts_multiple_failures():
    b = proj("/home/u/b2", git=None, errors=["git: second boom"])
    rows, caches = rows_of(FAIL, b)
    md = report.render(rows, caches, NOW)
    assert "## git unreadable (2)" in md
    assert "second boom" in md and "timed out" in md


def test_failed_project_does_not_vanish_from_report():
    rows, caches = rows_of(FAIL)
    assert "/home/u/broken" in report.render(rows, caches, NOW)


# ---- report host age prefers synced_at

def host_line(md, host):
    return [x for x in md.splitlines() if x.startswith("- %s:" % host)][0]


def test_report_age_uses_synced_at_when_remote_clock_is_ahead():
    c = cache("em-host", [], at=NOW + 5000, synced_at=NOW - 3600)
    md = report.render([], [c], NOW)
    line = host_line(md, "em-host")
    assert "synced 1h ago" in line
    assert "-" not in line.split("em-host:", 1)[1].split("ago")[0]


def test_report_age_never_negative_with_fast_remote_clock_only():
    c = cache("em-host", [], at=NOW + 5000, synced_at=NOW)
    line = host_line(report.render([], [c], NOW), "em-host")
    assert "-" not in line.split("em-host:", 1)[1]
    assert "-5" not in line


def test_report_without_synced_at_keeps_never_synced():
    c = cache("em-host", [], at=NOW - 7200)
    del c["synced_at"]
    line = host_line(report.render([], [c], NOW), "em-host")
    assert "never synced" in line


def test_report_age_from_synced_at_when_collected_at_is_5000s_fast():
    c = cache("em-host", [], at=NOW + 5000, synced_at=NOW - 7200)
    line = host_line(report.render([], [c], NOW), "em-host")
    assert "synced 2h ago" in line and "-" not in line.split("em-host:", 1)[1]


def test_report_age_synced_at_wins_over_older_collected_at():
    c = cache("em-host", [], at=NOW - 2 * DAY, synced_at=NOW - 3600)
    assert "synced 1h ago" in host_line(report.render([], [c], NOW), "em-host")


def test_report_data_freshness_section_age_from_synced_at_not_fast_collected_at():
    # M-3: the host age line in "## Data freshness" must use synced_at even when the
    # remote's collected_at runs 5000s ahead; whole-report render with projects present.
    fast = cache("em-host", [FAIL], at=NOW + 5000, synced_at=NOW - 7200,
                 stale_since=NOW - 7200, last_error="down")
    ok = cache("s-host", [proj("/home/u/ok")], at=NOW - 60, synced_at=NOW - 60)
    caches = [ok, fast]
    md = report.render(model.rows(caches, CFG, NOW), caches, NOW)
    sect = md.split("## Data freshness", 1)[1].split("\n## ", 1)[0]
    lines = [x for x in sect.splitlines() if x.startswith("- ")]
    em = [x for x in lines if x.startswith("- em-host:")]
    assert len(em) == 1
    assert "synced 2h ago" in em[0]
    assert "-" not in em[0].split("em-host:", 1)[1].replace("⚠ last sync failed: down", "")
    assert [x for x in lines if x.startswith("- s-host:")] == ["- s-host: synced 1m ago"]
