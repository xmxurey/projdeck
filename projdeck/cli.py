"""`proj` command line."""
import argparse
import errno
import json
import os
import shlex
import stat
import subprocess
import sys
import time

from projdeck import agent, board, config, fsutil, model, opener, report, serve, sync, termtext, ui

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
        print("%s: %s" % (s["host"], "ok" if s["ok"] else "FAILED (%s)" % termtext.one_line(s["error"])))


def _spawn_detached(marker, args, now=None):
    """Start `proj <args>` detached unless a sync holding `marker` is already running.
    Returns None, or a notice when the process could not be started."""
    problems = []
    if not sync.claim_marker(marker, now, problems):
        return "background sync not started: %s" % problems[0] if problems else None
    try:
        subprocess.Popen([sys.executable, "-m", "projdeck"] + args,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        sync.release_marker(marker)
        return "could not start background sync: %s" % e
    return None


_notices = []  # problems found while refreshing, shown in the fzf header by cmd_browse


def _note(text):
    _notices.append(termtext.one_line(text))


def flush_notices():
    """Print what is still queued to stderr, one line each; main() does it when any command
    ends, since a notice queued by then would never be read."""
    for text in take_notices():
        print(termtext.one_line(text), file=sys.stderr)


def usage_log_problem():
    """Why usage.jsonl is not being written (no path), or None when it is fine or can still be created."""
    path = os.path.join(sync.cache_dir(), "usage.jsonl")
    try:
        st = os.lstat(path)
    except OSError:
        # no file yet: the first append creates it, which needs a writable directory (the
        # nearest existing one; append_line makes the missing levels)
        d = os.path.dirname(path)
        while d and not os.path.exists(d):
            d = os.path.dirname(d)
        if d and not (os.path.isdir(d) and os.access(d, os.W_OK | os.X_OK)):
            return "directory not writable"
        return None
    if stat.S_ISLNK(st.st_mode):
        return "symbolic link"
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    if not os.access(path, os.W_OK):
        return "not writable"
    return None


def take_notices():
    out = list(_notices)
    del _notices[:]
    return out


def background_sync(now=None):
    return _spawn_detached(sync.SYNC_MARKER, ["sync", "--quiet"], now)


def background_live_sync(now=None):
    if sync.live_sync_running(now):
        return None
    return _spawn_detached(sync.LIVE_MARKER, ["sync", "--live", "--quiet"], now)


LOCAL_LIVE_SECONDS = 30
REMOTE_LIVE_SECONDS = 120


def fresh_lives(cfg, now):
    """Live caches for the terminal. The local host is recomputed in this process when its
    cache is older than LOCAL_LIVE_SECONDS; remote hosts older than REMOTE_LIVE_SECONDS start a background sync and
    keep what is cached. Never waits on ssh."""
    remote_due = False
    for h in cfg["hosts"]:
        c = sync.read_live(h["name"]) or {}
        age = now - (c.get("synced_at") or c.get("collected_at") or 0)
        if h.get("local"):
            if age > LOCAL_LIVE_SECONDS:
                sync.sync_live(h, cfg, now)
        elif age > REMOTE_LIVE_SECONDS:
            remote_due = True
    if remote_due:
        problem = background_live_sync(now)
        if problem:
            _note(problem)
    return sync.load_lives(cfg)


def dashboard_url(cfg):
    """Configured dashboard_url, else this node's Tailscale IP with the serve port, else None."""
    if cfg.get("dashboard_url"):
        return cfg["dashboard_url"]
    me = serve.tailscale_self()
    if me and me.get("ip"):
        return "http://%s:%d" % (me["ip"], serve.resolve_port(cfg, None))
    return None


def open_dashboard(cfg):
    """^V: open the dashboard in a browser; returns the notice to show."""
    url = dashboard_url(cfg)
    if not url:
        return "set dashboard_url in ~/.config/proj/config.json"
    if opener.open_url(url):
        return "opened %s" % termtext.one_line(url)
    if not url.startswith(("http://", "https://")):
        return "dashboard_url 不是 http(s) 地址，无法打开：%s" % termtext.one_line(url)
    return "看板：%s（在 Mac 浏览器里打开）" % termtext.one_line(url)


def fresh_caches(cfg, now):
    oldest = sync.oldest_sync(cfg)
    if oldest is None:
        print("first sync…", file=sys.stderr)
        print_sync(sync.sync_all(cfg))
    elif now - oldest > cfg["auto_sync_minutes"] * 60:
        problem = background_sync(now)
        if problem:
            _note(problem)
    return sync.load_caches(cfg)


_usage_warned = False  # the failed-usage-log notice is shown once per process


def _usage_failure(e):
    """Short reason for a failed usage-log write: an errno name, never the path."""
    if isinstance(e, OSError):
        if e.errno is None:
            return "not a regular file" if "not a regular file" in str(e) else "OSError"
        return errno.errorcode.get(e.errno) or "errno %d" % e.errno
    if isinstance(e, UnicodeEncodeError):
        return "not ASCII"
    return "ValueError"


def log_event(action, host, path, **extra):
    """Local-only usage log (~/.cache/proj/usage.jsonl), summarised by `proj report`.
    Returns False when the line was not written (a notice says so, once); never raises."""
    global _usage_warned
    ev = dict(ts=int(time.time()), action=action, host=host, path=path, **extra)
    try:
        fsutil.append_line(os.path.join(sync.cache_dir(), "usage.jsonl"), json.dumps(ev) + "\n")
    except (OSError, ValueError) as e:  # never let logging break the tool, but say so once
        if not _usage_warned:
            _usage_warned = True
            _note("usage log not written: %s" % _usage_failure(e))
        return False
    return True


def read_events(since):
    out = []
    try:
        with open(os.path.join(sync.cache_dir(), "usage.jsonl"), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                ts = ev.get("ts", 0)
                if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts >= since:
                    out.append(ev)  # a ts that is not a number is a damaged line: skipped
    except OSError:
        pass
    return out


def launch(row, path, session=None):
    new_tab = opener.has_iterm()
    inner = opener.resume_inner(session["id"]) if session else opener.PICKER
    cmd = opener.build_command(row, path, inner, new_tab=new_tab,
                               session_id=session["id"] if session else None)
    title = "%s:%s" % (row["host"], os.path.basename(path.rstrip("/")))
    log_event("resume" if session else "open", row["host"], row["path"])
    return opener.open_command(cmd, title, iterm=new_tab)  # "iterm" or "inline"


def open_project(rows, r):
    """Enter on a project. A local zellij host joins the project's live session (looked
    up now, not from the cache); without one, outside zellij, it creates the project's
    proj-<name> session, and where zellij is unusable the picker runs here. Returns
    (outcome, notice): "iterm"/"tab" keep proj open, "inline" means it ran here, None
    opened nothing."""
    name = "%s:%s" % (r["host"], os.path.basename(r["path"].rstrip("/")))
    if config.zellij_in_scope(r):
        paths = [x["path"] for x in rows if x["host"] == r["host"]]
        live = agent.zellij_by_project(paths).get(r["path"], [])
        if len(live) > 1:
            try:
                live = [ui.pick_live(live, name)]
            except ui.Cancelled:
                return None, None
        here = os.environ.get("ZELLIJ_SESSION_NAME")
        if live:
            s = live[0]["name"]
            if here:  # zellij cannot switch a client to another session from the CLI
                where = "this session" if s == here else "zellij session %s" % s
                return None, "%s is already open in %s: press Ctrl-o w to switch" % (name, where)
            try:
                cmd = opener.zellij_attach(s, r["path"])
            except ValueError:
                return None, "session name starts with '-', attach to it by hand"
            log_event("open", r["host"], r["path"], attach=s)
            return opener.open_command(cmd, name, iterm=False), None
        if here:
            try:
                opener.zellij_new_tab(r["path"], name)
            except (OSError, subprocess.CalledProcessError) as e:
                return None, "zellij new-tab failed: %s" % termtext.one_line(e)
            log_event("open", r["host"], r["path"])
            return "tab", "opened %s in a new zellij tab" % name
        if not os.environ.get("ZELLIJ"):
            return create_session(r, name), None
    return launch(r, r["path"]), None


def create_session(r, title):
    """Outside zellij, no live session of the project: start proj-<project name> running
    the picker. An exited session of that exact name is deleted first; any other session,
    exited or not, is never touched. Falls back to launch() when zellij is unusable."""
    sessions = agent.zellij_session_list()
    if sessions is None:
        return launch(r, r["path"])
    name = opener.proj_session_name(ui.project_name(r), r["path"], r.get("home"))
    state = opener.session_state(sessions, name)
    if state == "live":  # proj's own session whose processes are not in this project
        log_event("open", r["host"], r["path"], attach=name)
        return opener.open_command(opener.zellij_attach(name, r["path"]), title, iterm=False)
    layout_dir = os.path.join(sync.cache_dir(), "zellij")
    try:
        cmd = opener.zellij_new_session(r["path"], name, layout_dir, state == "exited")
    except (OSError, ValueError) as e:
        print("cannot write the zellij layout, running here instead: %s" % termtext.one_line(e),
              file=sys.stderr)
        return launch(r, r["path"])
    extra = {"replaced_exited": True} if state == "exited" else {}
    log_event("open", r["host"], r["path"], created=name, **extra)
    return opener.open_command(cmd, title, iterm=False)


def cmd_browse(cfg, args):
    """Project overview. Stays open after opening a tab in iTerm2, so you can switch
    between tasks; without iTerm2 the command runs here and proj exits."""
    take_notices()
    caches = fresh_caches(cfg, time.time())
    notice, last = "; ".join(cfg.get("warnings") or []) or None, None
    while True:
        now = time.time()
        rows = model.rows(caches, cfg, now)
        if not rows:
            print("no projects found; check roots in %s" % config.config_path())
            return 1
        try:
            bd = board.build(caches, fresh_lives(cfg, now), cfg, now)
            notice = "; ".join(x for x in [notice] + take_notices() if x) or None
            key, host, path = ui.pick_project(rows, now, termtext.one_line(notice) if notice else None,
                                              last, board=bd)
        except ui.Cancelled:
            return 0
        notice, last = None, (host, path)
        if key == "ctrl-u":
            notice = "sync: " + ", ".join(
                "%s %s" % (s["host"], "ok" if s["ok"] else "FAILED" + (
                    " (live)" if (s["error"] or "").startswith("live:") else ""))
                for s in sync.sync_all(cfg))
            caches = sync.load_caches(cfg)
            continue
        if key == "ctrl-l":
            try:
                last = ui.pick_backlog(rows, now)
            except ui.Cancelled:
                pass
            continue
        if key == "ctrl-v":
            notice = open_dashboard(cfg)
            continue
        if key and key not in ui.PROJECT_KEYS:
            continue  # unbound keys go back to the list
        session = None
        if key == "ctrl-f":  # works on host header lines too: it searches every host
            items = model.all_prompts(rows)
            if not items:
                notice = "no prompts cached; press ^U to sync"
                continue
            try:
                host, path, sid = ui.pick_prompt(items, now)
            except ui.Cancelled:
                continue
            last = (host, path)
        r = model.find_row(rows, host, path)
        if r is None:
            if host and path:  # only in the live data: no row to act on
                notice = "该项目还没采集，按 ^U 同步"
            continue  # (a host header line has no path)
        name = "%s:%s" % (host, os.path.basename(path.rstrip("/")))
        if key == "ctrl-x":
            try:
                notice = clean(cfg, host, path)
            except (sync.AgentError, ValueError) as e:
                notice = "clean failed: %s" % termtext.one_line(e)
            continue
        if key == "ctrl-f":
            session = next(s for s in r["sessions"] if s["id"] == sid)
        elif key == "ctrl-r":
            session = model.latest_session(r)
            if session is None:
                notice = "no sessions for %s" % name
                continue
        elif key == "ctrl-y":
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
        if session:
            outcome, msg = launch(r, session["cwd"], session), None
        else:
            outcome, msg = open_project(rows, r)
        if outcome == "inline":
            return 0
        notice = msg or ("opened %s%s in a new iTerm2 tab" % (name, " (resume)" if session else "")
                         if outcome == "iterm" else None)
        caches = sync.load_caches(cfg)


def cmd_sessions(cfg, args):
    now = time.time()
    rows = model.rows(fresh_caches(cfg, now), cfg, now)
    for problem in take_notices():
        print(problem, file=sys.stderr)
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
    for problem in take_notices():
        print(problem, file=sys.stderr)
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
    for name in args.hosts or ():
        config.host_by_name(cfg, name)  # unknown name: ValueError, exit 2
    status = sync.sync_all(cfg, args.hosts or None, live_only=args.live)
    if not args.quiet:
        print_sync(status)
    sync.release_marker(sync.LIVE_MARKER if args.live else sync.SYNC_MARKER)
    return 0 if all(s["ok"] for s in status) else 1


def cmd_report(cfg, args):
    now = time.time()
    caches = sync.load_caches(cfg)
    lives = sync.load_lives(cfg)
    bd = board.build(caches, lives, cfg, now)  # cache only: no fresh_lives, no zellij
    problem = usage_log_problem()
    if problem:
        print("usage log is not being written (%s); stats below may be stale" % problem)
    sys.stdout.write(report.render(model.rows(caches, cfg, now), caches, now,
                                   events=read_events(now - report.USAGE_DAYS * 86400), board=bd, lives=lives, cfg=cfg))
    return 0


def cmd_serve(cfg, args):
    return serve.serve(cfg, args.bind, args.port)


def clean(cfg, host_name, path):
    host = config.host_by_name(cfg, host_name)
    listing = sync.run_agent(host, {"action": "list", "path": path,
                                    "patterns": cfg["suspicious_patterns"]}, cfg["ssh_timeout"])
    if not listing["files"]:
        msg = "no untracked files in %s:%s" % (host_name, termtext.one_line(path))
        print(msg)
        return msg
    try:
        chosen = ui.pick_files(listing["files"], preview=ui.file_preview_cmd(host_name, listing["path"]))
    except ui.Cancelled:
        return "clean cancelled"
    if not chosen:
        return "clean: nothing selected"
    print("\n".join("  " + termtext.one_line(c) for c in chosen))
    if _ask("move %d file(s) to %s on %s? [y/N] " % (len(chosen), cfg["trash_dir"], host_name)) \
            .strip().lower() != "y":
        return "clean cancelled"
    res = sync.run_agent(host, {"action": "trash", "path": path, "files": chosen,
                                "trash_dir": cfg["trash_dir"]}, cfg["ssh_timeout"])
    log_event("clean", host_name, path, moved=len(res["moved"]))
    msg = "moved %d, skipped %d%s" % (len(res["moved"]), len(res["skipped"]),
                                      ("  (batch %s; undo: proj restore)" % termtext.one_line(res["batch"]))
                                      if res["batch"] else "")
    print(msg)
    for s in res["skipped"]:
        print("  skipped (no longer untracked or unsafe): %s" % termtext.one_line(s))
    return msg


def cmd_clean(cfg, args):
    host, sep, path = args.target.partition(":")
    if not sep:
        raise SystemExit("usage: proj clean HOST:PATH")
    clean(cfg, host, path)
    return 0


def _all_batches(cfg, host_name):
    hosts = [config.host_by_name(cfg, host_name)] if host_name else cfg["hosts"]
    out, failed = [], []
    for h in hosts:
        try:
            res = sync.run_agent(h, {"action": "batches", "trash_dir": cfg["trash_dir"]},
                                 cfg["ssh_timeout"])
        except sync.AgentError as e:
            print("%s: %s" % (h["name"], termtext.one_line(e)), file=sys.stderr)
            failed.append((h["name"], e))
            continue
        out.extend((h, b) for b in res["batches"])
    return out, failed


def _batch_text(h, b):
    """What a batch looks like on screen: every external part cleaned."""
    return "%s  %s  %s  %d file(s)  %s" % (termtext.one_line(h["name"]), termtext.one_line(b["id"]),
                                           ui.fmt_time(b["created"]), b["count"],
                                           termtext.one_line(b["project"]))


def _batch_line(h, b):
    """Picker line: host and batch id exactly as the agent gave them (restore sends the id
    back, and cleaning would change a name with double spaces or ESC), then the shown text."""
    return "%s\t%s\t%s" % (h["name"], ui.to_field(b["id"]), _batch_text(h, b))


def cmd_restore(cfg, args):
    batches, failed = _all_batches(cfg, args.host)
    if not batches:
        if failed:
            print("cannot read the trash on: %s" % ", ".join(termtext.one_line(n) for n, _ in failed),
                  file=sys.stderr)
            return 1
        print("trash is empty")
        return 0
    if failed:
        print("list incomplete: %d host(s) could not be read" % len(failed), file=sys.stderr)
    try:
        line = ui.pick_line([_batch_line(h, b) for h, b in batches], "pick a batch to restore",
                            shown_fields="3..")
    except ui.Cancelled:
        return 0
    host_name, batch_id = line.split("\t")[:2]
    batch_id = ui.from_field(batch_id)
    res = sync.run_agent(config.host_by_name(cfg, host_name),
                         {"action": "restore", "batch": batch_id, "trash_dir": cfg["trash_dir"]},
                         cfg["ssh_timeout"])
    log_event("restore", host_name, batch_id, restored=len(res["restored"]))
    print("restored %d, skipped %d" % (len(res["restored"]), len(res["skipped"])))
    for s in res["skipped"]:
        print("  skipped (path exists or trash copy missing): %s" % termtext.one_line(s))
    return 0


def _days(spec):
    spec = spec.strip().lower()
    try:
        days = float(spec[:-1]) if spec.endswith("d") else float(spec)
    except ValueError:
        raise SystemExit("--older must be a number of days such as 30d, got %r" % spec)
    if not config.positive_number(days):
        raise SystemExit("--older must be > 0")
    return days


def cmd_trash(cfg, args):
    batches, failed = _all_batches(cfg, args.host)
    if not args.purge:
        for h, b in batches:
            print(_batch_text(h, b))
        return 1 if failed else 0
    cutoff = time.time() - _days(args.older) * 86400
    old = [(h, b) for h, b in batches if b["created"] < cutoff]
    if not old:
        if failed:
            print("no older batches on the hosts that could be read; %d host(s) failed"
                  % len(failed))
            return 1
        print("nothing older than %s" % args.older)
        return 0
    for h, b in old:
        print(_batch_text(h, b))
    print("%d batch(es) will be permanently deleted." % len(old))
    if _ask("type yes to confirm: ").strip() != "yes":
        return 0
    hosts = {h["name"]: h for h, _ in old}
    failures = len(failed)
    for name, h in hosts.items():
        ids = [b["id"] for h2, b in old if h2["name"] == name]
        try:
            res = sync.run_agent(h, {"action": "purge", "ids": ids,
                                     "trash_dir": cfg["trash_dir"]}, cfg["ssh_timeout"])
        except sync.AgentError as e:
            print("%s: purge failed: %s" % (termtext.one_line(name), termtext.one_line(e)), file=sys.stderr)
            failures += 1
            continue
        print("%s: purged %d" % (name, len(res["purged"])))
    return 1 if failures else 0


def cmd_preview(cfg, args):
    now = time.time()
    host, path = args.host.rstrip("\t"), ui.from_field(args.path.rstrip("\t"))
    caches = sync.load_caches(cfg)
    if not path:
        print(ui.host_preview_text(host, caches, model.rows(caches, cfg, now), now))
        return 0
    rows = model.rows(caches, cfg, now)
    r, live = ui.preview_target(board.build(caches, sync.load_lives(cfg), cfg, now), rows, host, path, now)
    print(ui.preview_text(r, now, live) if r else "(not in cache)")
    return 0


def cmd_bpreview(cfg, args):
    now = time.time()
    host, path, idx = (a.rstrip("\t") for a in (args.host, args.path, args.idx))
    path = ui.from_field(path)
    caches = sync.load_caches(cfg)
    r = model.find_row(model.rows(caches, cfg, now), host, path)
    print(ui.backlog_preview_text(r, idx) if r else "(not in cache)")
    return 0


def cmd_spreview(cfg, args):
    now = time.time()
    host, path, sid = (a.rstrip("\t") for a in (args.host, args.path, args.sid))
    path, sid = ui.from_field(path), ui.from_field(sid)
    r = model.find_row(model.rows(sync.load_caches(cfg), cfg, now), host, path)
    s = next((s for s in r["sessions"] if s["id"] == sid), None) if r else None
    print(ui.session_preview_text(r, s, now, args.prompt.rstrip("\t")) if s else "(not in cache)")
    return 0


PREVIEW_BYTES = 8192


def _show(data, size_hint=None):
    if b"\0" in data[:PREVIEW_BYTES]:
        return "(binary file%s)" % ("" if size_hint is None else ", %d bytes" % size_hint)
    return termtext.safe(data[:PREVIEW_BYTES].decode("utf-8", "replace"))


def remote_preview_script(full):
    """sh script that prints a directory listing (ls -q: control characters, newlines
    included, come out as '?') or the head of a file."""
    p = shlex.quote(full)
    return "if [ -d %s ]; then ls -Aq %s | head -200; else head -c %d %s; fi" % (
        p, p, PREVIEW_BYTES, p)


def file_preview(host, full):
    """First few KB of a file (or a directory listing) on `host`, for the clean picker."""
    if host.get("local"):
        if os.path.isdir(full):
            names = sorted(os.listdir(full))
            return "\n".join(termtext.one_line(n) for n in names[:200]) + ("\n…" if len(names) > 200 else "")
        with open(full, "rb") as f:
            return _show(f.read(PREVIEW_BYTES), os.fstat(f.fileno()).st_size)
    script = remote_preview_script(full)
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host["ssh"], script],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
    return _show(r.stdout)


def cmd_fpreview(cfg, args):
    try:
        print(file_preview(config.host_by_name(cfg, args.host),
                           os.path.join(args.root, ui.from_field(args.rel.rstrip("\t")))), end="")
    except (OSError, subprocess.SubprocessError) as e:
        print("(cannot preview: %s)" % termtext.one_line(e))
    return 0


def parser():
    p = argparse.ArgumentParser(prog="proj", description="projects & Claude sessions across hosts")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("sessions", help="search all sessions by title and resume one")
    s = sub.add_parser("grep", help="search your own prompts in all sessions and resume one")
    s.add_argument("query", nargs="*")
    s = sub.add_parser("sync", help="refresh data from all (or given) hosts")
    s.add_argument("hosts", nargs="*")
    s.add_argument("--live", action="store_true", help="refresh only the live layer")
    s.add_argument("--quiet", action="store_true")
    sub.add_parser("report", help="markdown overview of what needs attention")
    s = sub.add_parser("serve", help="read-only web dashboard on the Tailscale address")
    s.add_argument("--bind", metavar="ADDR", help="Tailscale (100.64.0.0/10) or loopback IP")
    s.add_argument("--port", type=int, metavar="N", help="default: serve.port, else 8787")
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
    s = sub.add_parser("_bpreview")
    s.add_argument("host")
    s.add_argument("path")
    s.add_argument("idx", nargs="?", default="")
    s = sub.add_parser("_spreview")
    for a in ("host", "path", "sid", "prompt"):
        s.add_argument(a)
    s = sub.add_parser("_fpreview")
    s.add_argument("host")
    s.add_argument("root")
    s.add_argument("rel")
    return p


COMMANDS = {None: cmd_browse, "sessions": cmd_sessions, "grep": cmd_grep, "sync": cmd_sync,
            "report": cmd_report, "serve": cmd_serve, "clean": cmd_clean, "restore": cmd_restore,
            "trash": cmd_trash, "_preview": cmd_preview, "_bpreview": cmd_bpreview, "_spreview": cmd_spreview, "_fpreview": cmd_fpreview}


def main(argv=None):
    _env_setup()
    args = parser().parse_args(argv)
    try:
        cfg = config.load_config()
        return COMMANDS[args.cmd](cfg, args)
    except (ValueError, sync.AgentError) as e:
        print("proj: %s" % termtext.one_line(e), file=sys.stderr)
        return 2
    finally:
        flush_notices()  # whatever is still queued: no command that ends here shows the fzf header
