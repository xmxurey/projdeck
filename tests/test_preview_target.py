from projdeck import ui

NOW = 100 * 86400


def row(host="s-host", path="/home/u/quant"):
    return {"host": host, "path": path}


def sess(state, project="/home/u/new", host="s-host"):
    return {"id": "s1", "host": host, "project": project, "state": state, "title": "t",
            "started_at": NOW - 600, "idle_since": NOW - 60, "segments": []}


def test_cached_project_returns_its_row_and_no_live_when_silent():
    r = row()
    assert ui.preview_target({}, [r], "s-host", "/home/u/quant", NOW) == (r, None)


def test_live_only_project_gets_minimal_row_and_session():
    s = sess("waiting")
    r, live = ui.preview_target({"waiting": [s]}, [row()], "s-host", "/home/u/new", NOW)
    assert live is s and r["uncollected"] and r["path"] == "/home/u/new"


def test_most_urgent_fresh_session_represents_the_project():
    busy, waiting = sess("busy"), sess("waiting")
    _, live = ui.preview_target({"busy": [busy], "waiting": [waiting]}, [], "s-host", "/home/u/new", NOW)
    assert live is waiting


def test_stale_host_session_stands_in_when_no_fresh_one():
    s = sess("busy")
    _, live = ui.preview_target({"stale_host_sessions": [s]}, [], "s-host", "/home/u/new", NOW)
    assert live["id"] == "s1"


def test_unknown_everywhere_is_none():
    assert ui.preview_target({}, [row()], "s-host", "/nowhere", NOW) == (None, None)


def test_fresh_session_wins_over_stale_for_the_same_project():
    fresh, stale = sess("busy"), sess("busy")
    fresh["id"], stale["id"] = "fresh-one", "stale-one"
    _, live = ui.preview_target({"busy": [fresh], "stale_host_sessions": [stale]},
                                [], "s-host", "/home/u/new", NOW)
    assert live["id"] == "fresh-one"
