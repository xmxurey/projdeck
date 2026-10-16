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
    caches = [{"host": "mac", "projects": [proj("/old", mtime=NOW - 100 * DAY)]},
              {"host": "vm", "stale_since": 5, "projects": [proj("/new", mtime=NOW - DAY)]}]
    rows = model.rows(caches, CFG, NOW)
    assert [(r["host"], r["path"], r["tier"]) for r in rows] == [
        ("vm", "/new", "active"), ("mac", "/old", "abandoned")]
    assert rows[0]["ssh"] == "vm-alias" and rows[0]["local"] is False and rows[0]["stale_since"] == 5
    assert rows[1]["local"] is True
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
    assert model.human_age(30) == "now"
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
