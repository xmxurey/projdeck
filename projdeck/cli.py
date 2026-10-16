"""`proj` command line."""
import argparse
import os
import subprocess
import sys
import time

from projdeck import config, model, opener, report, sync, ui

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_setup():
    # children (fzf preview, background sync) run `python -m projdeck`
    rest = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    if rest and rest[0] == REPO_ROOT:
        return
    parts = [REPO_ROOT] + [p for p in rest if p != REPO_ROOT]
    os.environ["PYTHONPATH"] = os.pathsep.join(parts)


def _ask(prompt):
    """input() that treats EOF / Ctrl-C as an empty (i.e. "no") answer."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def print_sync(status):
    for s in status:
        print("%s: %s" % (s["host"], "ok" if s["ok"] else "FAILED (%s)" % s["error"]))


def background_sync():
    marker = os.path.join(sync.cache_dir(), ".syncing")
    try:
        if time.time() - os.path.getmtime(marker) < 300:
            return
    except OSError:
        pass
    os.makedirs(sync.cache_dir(), exist_ok=True)
    with open(marker, "w"):
        pass
    subprocess.Popen([sys.executable, "-m", "projdeck", "sync", "--quiet"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def fresh_caches(cfg, now):
    oldest = sync.oldest_sync(cfg)
    if oldest is None:
        print("first sync…", file=sys.stderr)
        print_sync(sync.sync_all(cfg))
    elif now - oldest > cfg["auto_sync_minutes"] * 60:
        background_sync()
    return sync.load_caches(cfg)


def launch(row, path, session=None):
    new_tab = opener.has_iterm()
    inner = opener.resume_inner(session["id"]) if session else None
    cmd = opener.build_command(row, path, inner, new_tab=new_tab)
    title = "%s:%s" % (row["host"], os.path.basename(path.rstrip("/")))
    opener.open_command(cmd, title, iterm=new_tab)
    return 0


def cmd_browse(cfg, args):
    now = time.time()
    caches = fresh_caches(cfg, now)
    while True:
        rows = model.rows(caches, cfg, now)
        if not rows:
            print("no projects found; check roots in %s" % config.config_path())
            return 1
        try:
            key, host, path = ui.pick_project(rows, now)
        except ui.Cancelled:
            return 0
        r = model.find_row(rows, host, path)
        if key == "ctrl-u":
            print_sync(sync.sync_all(cfg))
            caches, now = sync.load_caches(cfg), time.time()
            continue
        if key == "ctrl-x":
            return clean(cfg, host, path)
        if key == "ctrl-r":
            s = model.latest_session(r)
            if s is None:
                print("no sessions for this project")
                continue
            return launch(r, s["cwd"], s)
        if key == "ctrl-s":
            if not r["sessions"]:
                print("no sessions for this project")
                continue
            try:
                _, _, sid = ui.pick_session([(r, s) for s in r["sessions"]], now)
            except ui.Cancelled:
                continue
            s = next(s for s in r["sessions"] if s["id"] == sid)
            return launch(r, s["cwd"], s)
        if not r["exists"]:
            print("directory no longer exists: %s" % path)
            continue
        return launch(r, path)


def cmd_sessions(cfg, args):
    now = time.time()
    rows = model.rows(fresh_caches(cfg, now), cfg, now)
    pairs = model.all_sessions(rows)
    if not pairs:
        print("no sessions found")
        return 0
    try:
        host, path, sid = ui.pick_session(pairs, now)
    except ui.Cancelled:
        return 0
    r = model.find_row(rows, host, path)
    s = next(s for s in r["sessions"] if s["id"] == sid)
    return launch(r, s["cwd"], s)


def cmd_sync(cfg, args):
    status = sync.sync_all(cfg, args.hosts or None)
    if not args.quiet:
        print_sync(status)
    try:
        os.remove(os.path.join(sync.cache_dir(), ".syncing"))
    except OSError:
        pass
    return 0 if all(s["ok"] for s in status) else 1


def cmd_report(cfg, args):
    now = time.time()
    caches = sync.load_caches(cfg)
    sys.stdout.write(report.render(model.rows(caches, cfg, now), caches, now))
    return 0


def clean(cfg, host_name, path):
    host = config.host_by_name(cfg, host_name)
    listing = sync.run_agent(host, {"action": "list", "path": path,
                                    "patterns": cfg["suspicious_patterns"]}, cfg["ssh_timeout"])
    if not listing["files"]:
        print("no untracked files in %s:%s" % (host_name, path))
        return 0
    try:
        chosen = ui.pick_files(listing["files"])
    except ui.Cancelled:
        return 0
    if not chosen:
        return 0
    print("\n".join("  " + c for c in chosen))
    if _ask("move %d file(s) to %s on %s? [y/N] " % (len(chosen), cfg["trash_dir"], host_name)) \
            .strip().lower() != "y":
        return 0
    res = sync.run_agent(host, {"action": "trash", "path": path, "files": chosen,
                                "trash_dir": cfg["trash_dir"]}, cfg["ssh_timeout"])
    print("moved %d, skipped %d%s" % (len(res["moved"]), len(res["skipped"]),
                                       ("  (batch %s; undo: proj restore)" % res["batch"])
                                       if res["batch"] else ""))
    for s in res["skipped"]:
        print("  skipped (no longer untracked or unsafe): %s" % s)
    return 0


def cmd_clean(cfg, args):
    host, sep, path = args.target.partition(":")
    if not sep:
        raise SystemExit("usage: proj clean HOST:PATH")
    return clean(cfg, host, path)


def _all_batches(cfg, host_name):
    hosts = [config.host_by_name(cfg, host_name)] if host_name else cfg["hosts"]
    out = []
    for h in hosts:
        try:
            res = sync.run_agent(h, {"action": "batches", "trash_dir": cfg["trash_dir"]},
                                 cfg["ssh_timeout"])
        except sync.AgentError as e:
            print("%s: %s" % (h["name"], e), file=sys.stderr)
            continue
        out.extend((h, b) for b in res["batches"])
    return out


def _batch_line(h, b):
    return "%s\t%s\t%s  %d file(s)  %s" % (h["name"], b["id"], ui.fmt_time(b["created"]),
                                           b["count"], b["project"])


def cmd_restore(cfg, args):
    batches = _all_batches(cfg, args.host)
    if not batches:
        print("trash is empty")
        return 0
    try:
        line = ui.pick_line([_batch_line(h, b) for h, b in batches], "pick a batch to restore")
    except ui.Cancelled:
        return 0
    host_name, batch_id = line.split("\t")[:2]
    res = sync.run_agent(config.host_by_name(cfg, host_name),
                         {"action": "restore", "batch": batch_id, "trash_dir": cfg["trash_dir"]},
                         cfg["ssh_timeout"])
    print("restored %d, skipped %d" % (len(res["restored"]), len(res["skipped"])))
    for s in res["skipped"]:
        print("  skipped (path exists or trash copy missing): %s" % s)
    return 0


def _days(spec):
    spec = spec.strip().lower()
    return float(spec[:-1]) if spec.endswith("d") else float(spec)


def cmd_trash(cfg, args):
    batches = _all_batches(cfg, args.host)
    if not args.purge:
        for h, b in batches:
            print(_batch_line(h, b))
        return 0
    cutoff = time.time() - _days(args.older) * 86400
    old = [(h, b) for h, b in batches if b["created"] < cutoff]
    if not old:
        print("nothing older than %s" % args.older)
        return 0
    for h, b in old:
        print(_batch_line(h, b))
    print("%d batch(es) will be permanently deleted." % len(old))
    if _ask("type yes to confirm: ").strip() != "yes":
        return 0
    for h in {h["name"]: h for h, _ in old}.values():
        res = sync.run_agent(h, {"action": "purge", "older_days": _days(args.older),
                                 "trash_dir": cfg["trash_dir"]}, cfg["ssh_timeout"])
        print("%s: purged %d" % (h["name"], len(res["purged"])))
    return 0


def cmd_preview(cfg, args):
    now = time.time()
    r = model.find_row(model.rows(sync.load_caches(cfg), cfg, now), args.host.rstrip("\t"),
                       args.path.rstrip("\t"))
    print(ui.preview_text(r, now) if r else "(not in cache)")
    return 0


def parser():
    p = argparse.ArgumentParser(prog="proj", description="projects & Claude sessions across hosts")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("sessions", help="search all sessions by title and resume one")
    s = sub.add_parser("sync", help="refresh data from all (or given) hosts")
    s.add_argument("hosts", nargs="*")
    s.add_argument("--quiet", action="store_true")
    sub.add_parser("report", help="markdown overview of what needs attention")
    s = sub.add_parser("clean", help="move untracked junk to the trash (reversible)")
    s.add_argument("target", metavar="HOST:PATH")
    s = sub.add_parser("restore", help="restore a trash batch")
    s.add_argument("host", nargs="?")
    s = sub.add_parser("trash", help="list trash batches, or --purge old ones")
    s.add_argument("--purge", action="store_true")
    s.add_argument("--older", default="30d")
    s.add_argument("host", nargs="?")
    s = sub.add_parser("_preview")
    s.add_argument("host")
    s.add_argument("path")
    return p


COMMANDS = {None: cmd_browse, "sessions": cmd_sessions, "sync": cmd_sync, "report": cmd_report,
            "clean": cmd_clean, "restore": cmd_restore, "trash": cmd_trash,
            "_preview": cmd_preview}


def main(argv=None):
    _env_setup()
    args = parser().parse_args(argv)
    try:
        cfg = config.load_config()
        return COMMANDS[args.cmd](cfg, args)
    except (ValueError, sync.AgentError) as e:
        print("proj: %s" % e, file=sys.stderr)
        return 2
