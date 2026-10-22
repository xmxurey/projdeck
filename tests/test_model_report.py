from projdeck import model, report

DAY = 86400
NOW = 1000 * DAY
CFG = {"hosts": [{"name": "mac", "local": True}, {"name": "vm", "ssh": "vm-alias"}],
       "tiers_days": {"active": 14, "stale": 90}}


def proj(path, mtime=0, commit=0, sessions=(), exists=True, git=True, **kw):
    p = {"path": path, "exists": exists, "last_mtime": mtime, "suspicious": [], "progress": "",
         "sessions": list(sessions), "errors": [],
         "git": {"branch": "main", "dirty": 0, "untracked": 0, "ahead": 0, "no_upstream": False,
                 "last_commit_ts": commit} if git else None}
    p.update(kw)
    return p


def sess(sid, ts, headless=False):
    return {"id": sid, "cwd": "/x", "title": sid, "last_ts": ts, "size_kb": 1, "headless": headless}


def test_tiers_and_activity():
    t = CFG["tiers_days"]
    assert model.tier(proj("/a", mtime=NOW - 13 * DAY), NOW, t) == "active"
    assert model.tier(proj("/a", commit=NOW - 20 * DAY), NOW, t) == "stale"
    assert model.tier(proj("/a", sessions=[sess("s", NOW - DAY)]), NOW, t) == "active"
    assert model.tier(proj("/a", mtime=NOW - 91 * DAY), NOW, t) == "abandoned"
    assert model.tier(proj("/a", exists=False), NOW, t) == "deleted"


def test_rows_merge_hosts_and_sort():
    # grouped by host in config order (unknown hosts last), newest first within a host
    caches = [{"host": "zz", "projects": [proj("/z", mtime=NOW)]},
              {"host": "vm", "stale_since": 5, "projects": [proj("/new", mtime=NOW - DAY)]},
              {"host": "mac", "projects": [proj("/old", mtime=NOW - 100 * DAY),
                                           proj("/mid", mtime=NOW - 20 * DAY)]}]
    rows = model.rows(caches, CFG, NOW)
    assert [(r["host"], r["path"], r["tier"]) for r in rows] == [
        ("mac", "/mid", "stale"), ("mac", "/old", "abandoned"), ("vm", "/new", "active"),
        ("zz", "/z", "active")]
    assert rows[2]["ssh"] == "vm-alias" and rows[2]["local"] is False and rows[2]["stale_since"] == 5
    assert rows[0]["local"] is True
    assert model.find_row(rows, "mac", "/old") is rows[1]
    assert model.find_row(rows, "mac", "/nope") is None


def test_sessions_helpers():
    r = proj("/a", sessions=[sess("cron", 30, headless=True), sess("mine", 20)])
    assert model.latest_session(r)["id"] == "mine"
    assert model.latest_session(proj("/b", sessions=[sess("cron", 1, True)]))["id"] == "cron"
    assert model.latest_session(proj("/c")) is None
    rows = model.rows([{"host": "mac", "projects": [r, proj("/d", sessions=[sess("d", 25)])]}],
                      CFG, NOW)
    assert [s["id"] for _, s in model.all_sessions(rows)] == ["cron", "d", "mine"]


def test_human_age():
    assert model.human_age(30) == "<1m"
    assert model.human_age(5 * 60) == "5m"
    assert model.human_age(3 * 3600) == "3h"
    assert model.human_age(3 * DAY) == "3d"
    assert model.human_age(65 * DAY) == "2mo"
    assert model.human_age(400 * DAY) == "1y"


def test_report_sections():
    caches = [{"host": "mac", "synced_at": NOW - 3600, "stale_since": None, "projects": [
        proj("/dirty", mtime=NOW, git=True, suspicious=["a.log", "b.log"]),
        proj("/gone", exists=False), proj("/old", mtime=NOW - 200 * DAY)]},
        {"host": "vm", "synced_at": NOW - 7200, "stale_since": NOW - 7200,
         "last_error": "timeout", "projects": []}]
    caches[0]["projects"][0]["git"]["dirty"] = 3
    rows = model.rows(caches, CFG, NOW)
    md = report.render(rows, caches, NOW)
    assert "- mac: synced 1h ago" in md
    assert "- vm: synced 2h ago ⚠ last sync failed: timeout" in md
    assert "`mac:/dirty` ±3" in md
    assert "`mac:/dirty` 2 files: a.log, b.log" in md
    assert "`mac:/gone`" in md and "`mac:/old` 6mo" in md


def test_suspicious_count_prefers_total_and_report_tolerates_missing_keys():
    p = proj("/many", mtime=NOW, git=True, suspicious=["a.log"])
    p["suspicious_count"] = 250
    bare = {"path": "/bare", "exists": True, "last_mtime": NOW, "sessions": []}  # older agent output
    caches = [{"host": "mac", "synced_at": NOW, "stale_since": None, "projects": [p, bare]}]
    rows = model.rows(caches, CFG, NOW)
    assert model.n_suspicious(rows[0]) == 250 and model.n_suspicious({}) == 0
    md = report.render(rows, caches, NOW)
    assert "`mac:/many` 250 files: a.log" in md


def test_never_synced_host_is_not_56_years_old():
    caches = [{"host": "vm", "synced_at": 0, "stale_since": NOW, "last_error": "down",
               "projects": []}]
    md = report.render([], caches, NOW)
    assert "- vm: never synced ⚠ last sync failed: down" in md
    assert model.sync_age(caches[0], NOW) == "never synced"
    assert model.sync_age({"synced_at": NOW - 3600}, NOW) == "synced 1h ago"


def test_report_no_upstream_and_suspicious_cap():
    projects = [proj("/p%02d" % i, mtime=NOW, suspicious=["x%d.log" % j for j in range(i + 1)])
                for i in range(20)]
    projects[0]["git"]["no_upstream"] = True
    caches = [{"host": "mac", "synced_at": NOW, "stale_since": None, "projects": projects}]
    md = report.render(model.rows(caches, CFG, NOW), caches, NOW)
    assert "## No upstream branch (1)\n\n- `mac:/p00`\n" in md
    assert "## Suspicious untracked files (15)" in md
    assert "`mac:/p19` 20 files: x0.log, x1.log, x2.log, x3.log, x4.log\n" in md
    assert "`mac:/p04`" not in md.split("## Suspicious")[1].split("## Abandoned")[0]
