"""The auto-delete note only goes on sessions named by the current naming rule."""
import shlex

from projdeck import opener, report, ui

NOW = 1790000000


def exited(*names, host="s-host"):
    return [{"host": host, "name": n, "cmd": "zellij delete-session " + shlex.quote(n)}
            for n in names]


def section(names, rows):
    board = {"summary": {"zellij_exited": exited(*names)}}
    lives = [{"host": "s-host", "zellij_exited": list(names), "synced_at": NOW - 5}]
    cfg = {"hosts": [{"name": "s-host", "local": True, "mux": "zellij"}]}
    return report._exited_section(board, lives, cfg, NOW, rows)


def row(path, host="s-host", home="/home/u"):
    return {"host": host, "path": path, "home": home}


def line_for(lines, name):
    return [ln for ln in lines if shlex.quote(name) in ln][0]


def test_current_name_gets_the_note():
    r = row("/home/u/dao")
    name = opener.proj_session_name(ui.project_name(r), r["path"], r["home"])
    assert report.PROJ_NOTE in line_for(section([name], [r]), name)


def test_old_format_name_of_nested_project_gets_no_note():
    r = row("/home/u/dev/projdeck")
    new = opener.proj_session_name(ui.project_name(r), r["path"], r["home"])
    assert new != "proj-dev-projdeck"
    lines = section(["proj-dev-projdeck", new], [r])
    assert report.PROJ_NOTE not in line_for(lines, "proj-dev-projdeck")
    assert report.PROJ_NOTE in line_for(lines, new)


def test_proj_prefix_without_any_project_gets_no_note():
    assert report.PROJ_NOTE not in line_for(section(["proj-gone"], []), "proj-gone")


def test_other_hosts_projects_do_not_count():
    r = row("/home/u/dao", host="em-host")
    name = opener.proj_session_name(ui.project_name(r), r["path"], r["home"])
    assert report.PROJ_NOTE not in line_for(section([name], [r]), name)
