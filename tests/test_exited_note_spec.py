"""Independent spec tests for T11: the auto-delete note on exited zellij sessions only for names
equal to a project's current session name; agent.attach_map gives no command for names with a
leading '-'."""
import hashlib
import os

import pytest

from projdeck import agent, model, opener, report, ui
from projdeck import board as board_mod

NOW = 1795000000
NOTE = "proj 下次在该项目按 Enter 时会自动删除"
H1 = {"name": "s-host", "local": True, "mux": "zellij"}
H2 = {"name": "em-host", "local": True, "mux": "zellij"}
HOME = "/home/u"


def cache(host="s-host"):
    return {"schema": 1, "host": host, "collected_at": NOW - 60, "synced_at": NOW - 60,
            "stale_since": None, "last_error": None, "projects": []}


def live(host, exited):
    return {"schema": 1, "host": host, "collected_at": NOW - 10, "source": "claude agents",
            "since": NOW - 86400, "sessions": [], "projects": {}, "zellij_exited": list(exited)}


def row(path, host="s-host", home=HOME):
    return dict(model.ROW_DEFAULTS, host=host, path=path, home=home, tier="active", activity=NOW)


def current_name(r):
    return opener.proj_session_name(ui.project_name(r), r["path"], r.get("home"))


def entry_lines(hosts, rows, exited_by_host):
    cfg = {"hosts": hosts, "tiers_days": {"active": 14, "stale": 90}}
    caches = [cache(h["name"]) for h in hosts]
    lives = [live(h["name"], exited_by_host.get(h["name"], [])) for h in hosts]
    b = board_mod.build(caches, lives, cfg, NOW)
    md = report.render(rows, caches, NOW, board=b, lives=lives, cfg=cfg)
    sec = md.split("## Exited zellij sessions", 1)[1].split("\n## ", 1)[0]
    return [ln for ln in sec.split("\n") if ln.startswith("- `")]


def one_host(rows, exited):
    return entry_lines([H1], rows, {"s-host": exited})


def line_for(lines, name, host="s-host"):
    got = [ln for ln in lines if ln.startswith("- `%s`:" % host) and ("-- %s`" % name in ln)]
    assert len(got) == 1, (name, lines)
    return got[0]


# ---- acceptance 1 ----

def test_plain_current_name_gets_note():
    r = row("/home/u/foo")
    assert current_name(r) == "proj-foo"
    ls = one_host([r], ["proj-foo"])
    assert NOTE in line_for(ls, "proj-foo")


def test_hashed_current_name_gets_note():
    # nested name contains "/", so the current name carries the path hash
    r = row("/home/u/dev/projdeck")
    digest = hashlib.sha1(b"/home/u/dev/projdeck").hexdigest()[:4]
    cur = current_name(r)
    assert cur == "proj-dev-projdeck-" + digest
    ls = one_host([r], [cur])
    assert NOTE in line_for(ls, cur)


def test_outside_home_hashed_name_gets_note():
    r = row("/srv/x/work", home=HOME)
    cur = current_name(r)
    assert cur.startswith("proj-work-") and len(cur) == len("proj-work-") + 4
    assert NOTE in line_for(one_host([r], [cur]), cur)


def test_old_format_name_of_nested_project_gets_no_note():
    r = row("/home/u/dev/projdeck")
    ls = one_host([r], ["proj-dev-projdeck"])
    assert len(ls) == 1
    assert "自动删除" not in ls[0] and "zellij delete-session -- proj-dev-projdeck" in ls[0]


def test_old_format_name_gets_no_note_even_with_other_projects_present():
    rows = [row("/home/u/dev/projdeck"), row("/home/u/foo")]
    ls = one_host(rows, ["proj-dev-projdeck", "proj-foo"])
    assert "自动删除" not in line_for(ls, "proj-dev-projdeck")
    assert NOTE in line_for(ls, "proj-foo")


def test_proj_prefix_matching_no_project_gets_no_note():
    ls = one_host([row("/home/u/foo")], ["proj-ghost"])
    assert "自动删除" not in ls[0]


def test_no_rows_means_no_note():
    ls = one_host([], ["proj-foo"])
    assert len(ls) == 1 and "自动删除" not in ls[0]


def test_user_named_session_gets_no_note():
    ls = one_host([row("/home/u/foo")], ["foo", "mine", "Proj-foo", "proj-foo2", "xproj-foo"])
    assert len(ls) == 5
    assert all("自动删除" not in ln for ln in ls)


def test_note_is_per_session_line_exact():
    ls = one_host([row("/home/u/foo")], ["proj-foo", "mine"])
    assert NOTE in line_for(ls, "proj-foo")
    assert "自动删除" not in line_for(ls, "mine")


def test_hash_of_other_path_gets_no_note():
    r = row("/home/u/dev/projdeck")
    good = hashlib.sha1(b"/home/u/dev/projdeck").hexdigest()[:4]
    bad = "0000" if good != "0000" else "1111"
    ls = one_host([r], ["proj-dev-projdeck-" + bad])
    assert "自动删除" not in ls[0]


def test_multiple_projects_each_current_name_matches():
    rows = [row("/home/u/a"), row("/home/u/b/c"), row("/srv/z")]
    names = [current_name(r) for r in rows]
    ls = one_host(rows, names + ["proj-b-c"])
    for n in names:
        assert NOTE in line_for(ls, n)
    assert "自动删除" not in line_for(ls, "proj-b-c")


def test_hosts_match_separately():
    rows = [row("/home/u/foo", host="s-host")]
    ls = entry_lines([H1, H2], rows, {"s-host": ["proj-foo"], "em-host": ["proj-foo"]})
    s = line_for(ls, "proj-foo", "s-host")
    e = line_for(ls, "proj-foo", "em-host")
    assert NOTE in s
    assert "自动删除" not in e


def test_hosts_match_separately_both_projects():
    rows = [row("/home/u/foo", host="s-host"), row("/home/u/bar", host="em-host")]
    ls = entry_lines([H1, H2], rows, {"s-host": ["proj-foo", "proj-bar"],
                                      "em-host": ["proj-foo", "proj-bar"]})
    assert NOTE in line_for(ls, "proj-foo", "s-host")
    assert "自动删除" not in line_for(ls, "proj-bar", "s-host")
    assert NOTE in line_for(ls, "proj-bar", "em-host")
    assert "自动删除" not in line_for(ls, "proj-foo", "em-host")


def test_hosts_with_different_homes_hash_difference():
    # same path outside each host's home: the name only depends on the path, so equal names
    r1 = row("/srv/w", host="s-host", home="/home/u")
    r2 = row("/home/x/w", host="em-host", home="/home/x")
    assert current_name(r1) != current_name(r2)
    ls = entry_lines([H1, H2], [r1, r2], {"s-host": [current_name(r2)],
                                          "em-host": [current_name(r1)]})
    assert all("自动删除" not in ln for ln in ls)


# ---- acceptance 2 ----

def fake_proc(root, procs):
    for pid, (ppid, comm, argv) in procs.items():
        d = root / str(pid)
        d.mkdir()
        (d / "stat").write_text("%d (%s) S %d 1 1 0 -1\n" % (pid, comm, ppid))
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "comm").write_text(comm + "\n")
        os.symlink("/", str(d / "cwd"))
    return str(root)


SERVER = "/home/u/.local/bin/zellij"
SOCK = "/run/user/1000/zellij/contract_version_1/"


@pytest.mark.parametrize("bad", ["-evil", "--help", "-", "-x y", "--session=foo"])
def test_attach_map_empty_for_dash_names(tmp_path, bad):
    proc = fake_proc(tmp_path, {
        10: (1, "zellij", [SERVER, "--server", SOCK + bad]),
        11: (10, "claude", ["claude"]),
        12: (11, "claude", ["claude"]),
    })
    got = agent.attach_map(proc)
    for pid in (11, 12):
        assert got.get(pid, "") == ""
    assert bad not in " ".join(str(v) for v in got.values())


def test_attach_map_good_name_still_attaches_next_to_bad(tmp_path):
    proc = fake_proc(tmp_path, {
        10: (1, "zellij", [SERVER, "--server", SOCK + "-evil"]),
        11: (10, "claude", ["claude"]),
        20: (1, "zellij", [SERVER, "--server", SOCK + "proj-ok"]),
        21: (20, "claude", ["claude"]),
        30: (1, "zellij", [SERVER, "--server", SOCK + "a-b"]),
        31: (30, "claude", ["claude"]),
    })
    got = agent.attach_map(proc)
    assert got[21] == "zellij attach proj-ok"
    assert got[31] == "zellij attach a-b"
    assert got.get(11, "") == ""
    assert "-evil" not in " ".join(str(v) for v in got.values())
