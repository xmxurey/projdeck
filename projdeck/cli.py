"""`proj` command line."""
import argparse
import json
import os
import shlex
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


def log_event(action, host, path, **extra):
    """Local-only usage log (~/.cache/proj/usage.jsonl), summarised by `proj report`."""
    ev = dict(ts=int(time.time()), action=action, host=host, path=path, **extra)
    try:
        os.makedirs(sync.cache_dir(), exist_ok=True)
        with open(os.path.join(sync.cache_dir(), "usage.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass  # never let logging break the tool


def read_events(since):
    out = []
    try:
        with open(os.path.join(sync.cache_dir(), "usage.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("ts", 0) >= since:
                    out.append(ev)
    except OSError:
        pass
    return out


def launch(row, path, session=None):
    new_tab = opener.has_iterm()
    inner = opener.resume_inner(session["id"]) if session else None
    cmd = opener.build_command(row, path, inner, new_tab=new_tab,
                               session_id=session["id"] if session else None)
    title = "%s:%s" % (row["host"], os.path.basename(path.rstrip("/")))
    log_event("resume" if session else "open", row["host"], row["path"])
    return opener.open_command(cmd, title, iterm=new_tab)  # "iterm" or "inline"


def cmd_browse(cfg, args):
    """Project overview. Stays open after opening a tab in iTerm2, so you can switch
    between tasks; without iTerm2 the command runs here and proj exits."""
    now = time.time()
    caches = fresh_caches(cfg, now)
    notice = last = None
    while True:
        rows = model.rows(caches, cfg, now)
        if not rows:
            print("no projects found; check roots in %s" % config.config_path())
            return 1
        try:
            key, host, path = ui.pick_project(rows, now, notice, last)
        except ui.Cancelled:
            return 0
        notice, last = None, (host, path)
        if key == "ctrl-u":
            notice = "sync: " + ", ".join("%s %s" % (s["host"], "ok" if s["ok"] else "FAILED")
                                          for s in sync.sync_all(cfg))
            caches, now = sync.load_caches(cfg), time.time()
            continue
        r = model.find_row(rows, host, path)
        if r is None:  # a host header line
            continue
        name = "%s:%s" % (host, os.path.basename(path.rstrip("/")))
        if key == "ctrl-x":
            notice = clean(cfg, host, path)
            continue
        session = None
        if key == "ctrl-r":
            session = model.latest_session(r)
            if session is None:
                notice = "no sessions for %s" % name
                continue
        elif key == "ctrl-s":
            if not r["sessions"]:
                notice = "no sessions for %s" % name
                continue
            try:
                _, _, sid = ui.pick_session([(r, s) for s in r["sessions"]], now)
            except ui.Cancelled:
                continue
            session = next(s for s in r["sessions"] if s["id"] == sid)
        elif not r["exists"]:
            notice = "directory no longer exists: %s" % path
            continue
        if launch(r, session["cwd"] if session else path, session) != "iterm":
            return 0
        notice = "opened %s%s in a new iTerm2 tab" % (name, " (resume)" if session else "")
        caches, now = sync.load_caches(cfg), time.time()


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
    launch(r, s["cwd"], s)
    return 0


def cmd_grep(cfg, args):
    now = time.time()
    rows = model.rows(fresh_caches(cfg, now), cfg, now)
    items = model.all_prompts(rows)
    if not items:
        print("no prompts cached; run proj sync")
        return 0
    try:
        host, path, sid = ui.pick_prompt(items, now, " ".join(args.query))
    except ui.Cancelled:
        return 0
    r = model.find_row(rows, host, path)
    s = next(s for s in r["sessions"] if s["id"] == sid)
    launch(r, s["cwd"], s)
    return 0


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
    sys.stdout.write(report.render(model.rows(caches, cfg, now), caches, now,
                                   events=read_events(now - report.USAGE_DAYS * 86400)))
    return 0


def clean(cfg, host_name, path):
    host = config.host_by_name(cfg, host_name)
    listing = sync.run_agent(host, {"action": "list", "path": path,
                                    "patterns": cfg["suspicious_patterns"]}, cfg["ssh_timeout"])
    if not listing["files"]:
        msg = "no untracked files in %s:%s" % (host_name, path)
        print(msg)
        return msg
    try:
        chosen = ui.pick_files(listing["files"], preview=ui.file_preview_cmd(host_name, listing["path"]))
    except ui.Cancelled:
        return "clean cancelled"
    if not chosen:
        return "clean: nothing selected"
    print("\n".join("  " + c for c in chosen))
    if _ask("move %d file(s) to %s on %s? [y/N] " % (len(chosen), cfg["trash_dir"], host_name)) \
            .strip().lower() != "y":
        return "clean cancelled"
    res = sync.run_agent(host, {"action": "trash", "path": path, "files": chosen,
                                "trash_dir": cfg["trash_dir"]}, cfg["ssh_timeout"])
    log_event("clean", host_name, path, moved=len(res["moved"]))
    msg = "moved %d, skipped %d%s" % (len(res["moved"]), len(res["skipped"]),
                                      ("  (batch %s; undo: proj restore)" % res["batch"])
                                      if res["batch"] else "")
    print(msg)
    for s in res["skipped"]:
        print("  skipped (no longer untracked or unsafe): %s" % s)
    return msg


def cmd_clean(cfg, args):
    host, sep, path = args.target.partition(":")
    if not sep:
        raise SystemExit("usage: proj clean HOST:PATH")
    clean(cfg, host, path)
    return 0


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
    log_event("restore", host_name, batch_id, restored=len(res["restored"]))
    print("restored %d, skipped %d" % (len(res["restored"]), len(res["skipped"])))
    for s in res["skipped"]:
        print("  skipped (path exists or trash copy missing): %s" % s)
    return 0


def _days(spec):
    spec = spec.strip().lower()
    days = float(spec[:-1]) if spec.endswith("d") else float(spec)
    if days <= 0:
        raise SystemExit("--older must be > 0")
    return days


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
    hosts = {h["name"]: h for h, _ in old}
    for name, h in hosts.items():
        ids = [b["id"] for h2, b in old if h2["name"] == name]
        res = sync.run_agent(h, {"action": "purge", "ids": ids,
                                 "trash_dir": cfg["trash_dir"]}, cfg["ssh_timeout"])
        print("%s: purged %d" % (name, len(res["purged"])))
    return 0


def cmd_preview(cfg, args):
    now = time.time()
    host, path = args.host.rstrip("\t"), args.path.rstrip("\t")
    caches = sync.load_caches(cfg)
    if not path:
        print(ui.host_preview_text(host, caches, model.rows(caches, cfg, now), now))
        return 0
    r = model.find_row(model.rows(caches, cfg, now), host, path)
    print(ui.preview_text(r, now) if r else "(not in cache)")
    return 0


def cmd_spreview(cfg, args):
    now = time.time()
    host, path, sid = (a.rstrip("\t") for a in (args.host, args.path, args.sid))
    r = model.find_row(model.rows(sync.load_caches(cfg), cfg, now), host, path)
    s = next((s for s in r["sessions"] if s["id"] == sid), None) if r else None
    print(ui.session_preview_text(r, s, now, args.prompt.rstrip("\t")) if s else "(not in cache)")
    return 0


PREVIEW_BYTES = 8192


def _show(data, size_hint=None):
    if b"\0" in data[:PREVIEW_BYTES]:
        return "(binary file%s)" % ("" if size_hint is None else ", %d bytes" % size_hint)
    return data[:PREVIEW_BYTES].decode("utf-8", "replace")


def file_preview(host, full):
    """First few KB of a file (or a directory listing) on `host`, for the clean picker."""
    if host.get("local"):
        if os.path.isdir(full):
            names = sorted(os.listdir(full))
            return "\n".join(names[:200]) + ("\n…" if len(names) > 200 else "")
        with open(full, "rb") as f:
            return _show(f.read(PREVIEW_BYTES), os.fstat(f.fileno()).st_size)
    p = shlex.quote(full)
    script = "if [ -d %s ]; then ls -A %s | head -200; else head -c %d %s; fi" % (
        p, p, PREVIEW_BYTES, p)
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host["ssh"], script],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
    return _show(r.stdout)


def cmd_fpreview(cfg, args):
    try:
        print(file_preview(config.host_by_name(cfg, args.host),
                           os.path.join(args.root, args.rel.rstrip("\t"))), end="")
    except (OSError, subprocess.SubprocessError) as e:
        print("(cannot preview: %s)" % e)
    return 0


def parser():
    p = argparse.ArgumentParser(prog="proj", description="projects & Claude sessions across hosts")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("sessions", help="search all sessions by title and resume one")
    s = sub.add_parser("grep", help="search your own prompts in all sessions and resume one")
    s.add_argument("query", nargs="*")
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
    s = sub.add_parser("_spreview")
    for a in ("host", "path", "sid", "prompt"):
        s.add_argument(a)
    s = sub.add_parser("_fpreview")
    s.add_argument("host")
    s.add_argument("root")
    s.add_argument("rel")
    return p


COMMANDS = {None: cmd_browse, "sessions": cmd_sessions, "grep": cmd_grep, "sync": cmd_sync,
            "report": cmd_report, "clean": cmd_clean, "restore": cmd_restore, "trash": cmd_trash,
            "_preview": cmd_preview, "_spreview": cmd_spreview, "_fpreview": cmd_fpreview}


def main(argv=None):
    _env_setup()
    args = parser().parse_args(argv)
    try:
        cfg = config.load_config()
        return COMMANDS[args.cmd](cfg, args)
    except (ValueError, sync.AgentError) as e:
        print("proj: %s" % e, file=sys.stderr)
        return 2
