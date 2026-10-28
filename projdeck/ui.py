"""fzf-based pickers and the preview pane."""
import os
import shlex
import subprocess
import sys
import time

from projdeck.model import TIER_ICON, human_age, latest_session, n_suspicious, sync_age

SEP = "\t"
PROJECT_KEYS = ["ctrl-r", "ctrl-s", "ctrl-x", "ctrl-u"]
PROJECT_HEADER = "Enter: open · ^R: resume latest · ^S: pick session · ^X: clean · ^U: sync"


class Cancelled(Exception):
    pass


def run_fzf(lines, args, expect=()):
    argv = ["fzf"] + list(args)
    if expect:
        argv.append("--expect=" + ",".join(expect))
    try:
        r = subprocess.run(argv, input="\n".join(lines), stdout=subprocess.PIPE,
                           encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit("fzf is required: brew install fzf")
    if r.returncode in (1, 130):  # 1: no match, 130: aborted (Esc / Ctrl-C)
        raise Cancelled()
    if r.returncode != 0:
        raise SystemExit("fzf failed (exit %d); fzf >= 0.36 is required" % r.returncode)
    out = r.stdout.split("\n")
    key = out.pop(0) if expect else ""
    return key, [line for line in out if line]


def preview_cmd():
    return "%s -m projdeck _preview {1} {2}" % shlex.quote(sys.executable)


def spreview_cmd():
    return "%s -m projdeck _spreview {1} {2} {3} {4}" % shlex.quote(sys.executable)


def file_preview_cmd(host_name, root):
    return "%s -m projdeck _fpreview %s %s {1}" % (shlex.quote(sys.executable),
                                                   shlex.quote(host_name), shlex.quote(root))


def _name(path):
    return os.path.basename(path.rstrip("/")) or path


def project_line(r, now):
    g = r.get("git") or {}
    flags = []
    if r.get("live_tmux"):
        flags.append("▶%d" % len(r["live_tmux"]))
    if g.get("dirty"):
        flags.append("±%d" % g["dirty"])
    if g.get("ahead"):
        flags.append("↑%d" % g["ahead"])
    if r.get("git") and g.get("no_upstream"):
        flags.append("⊘")
    if n_suspicious(r):
        flags.append("⚑%d" % n_suspicious(r))
    if r["sessions"]:
        flags.append("💬%d" % len(r["sessions"]))
    if not r.get("git") and r["exists"]:
        flags.append("non-git")
    display = "%s %-28s %5s  %s" % (TIER_ICON[r["tier"]], _name(r["path"])[:28],
                                    human_age(now - r["activity"]), " ".join(flags))
    return SEP.join([r["host"], r["path"], display])


def project_lines(rows, now):
    """Project lines with a header line (empty path field) before each host's group."""
    lines, host = [], None
    for r in rows:
        if r["host"] != host:
            host = r["host"]
            lines.append(SEP.join([host, "", "── %s %s" % (host, "─" * max(3, 40 - len(host)))]))
        lines.append(project_line(r, now))
    return lines


def session_line(r, s, now):
    display = "%-8s %5s  %-20s %s%s" % (r["host"][:8], human_age(now - s["last_ts"]),
                                        _name(r["path"])[:20], s["title"],
                                        "  [headless]" if s.get("headless") else "")
    return SEP.join([r["host"], r["path"], s["id"], display])


def prompt_line(r, s, text, now):
    where = "%s:%s" % (r["host"], _name(r["path"]))
    display = "%5s  %-28s │ %s" % (human_age(now - s["last_ts"]), where[:28], text)
    return SEP.join([r["host"], r["path"], s["id"], text, display])


def file_line(f):
    return SEP.join([f["path"], "%s %s  (%dK)" % ("⚑" if f["suspicious"] else " ", f["path"],
                                                   f["size"] // 1024)])


def _left_off(s, now):
    if not (s.get("recent") or s.get("last_reply")):
        return []
    out = ["", "left off · %s · %s ago" % (s["title"], human_age(now - s["last_ts"]))]
    out.extend("  › " + p for p in s.get("recent") or [])
    if s.get("last_reply"):
        out.append("  ⏺ " + s["last_reply"])
    return out


def session_preview_text(r, s, now, prompt):
    return "\n".join(["› " + prompt, "", "%s:%s" % (r["host"], r["path"]),
                      "session: %s · %s ago" % (s["title"], human_age(now - s["last_ts"]))]
                     + _left_off(s, now))


def preview_text(r, now):
    out = ["%s:%s" % (r["host"], r["path"]),
           "%s %s · last activity %s ago" % (TIER_ICON[r["tier"]], r["tier"],
                                             human_age(now - r["activity"]))]
    if r.get("stale_since"):
        out.append("⚠ host data is from %s ago (last sync failed)" % human_age(now - r["stale_since"]))
    g = r.get("git")
    if g:
        out.append("git: %s  ±%d ↑%d  untracked %d%s  last commit %s ago" % (
            g["branch"], g["dirty"], g["ahead"], g["untracked"],
            "  (no upstream)" if g["no_upstream"] else "", human_age(now - g["last_commit_ts"])))
    elif r["exists"]:
        out.append("not a git repo")
    else:
        out.append("directory no longer exists")
    if r.get("live_tmux"):
        out.append("▶ live tmux: %s" % ", ".join(r["live_tmux"]))
    s = latest_session(r)
    if s:
        out.extend(_left_off(s, now))
    if r["suspicious"]:
        out.append("")
        out.append("suspicious files (%d):" % n_suspicious(r))
        out.extend("  " + s for s in r["suspicious"][:10])
    if r["progress"]:
        out.extend(["", "progress:"] + ["  " + line for line in r["progress"].splitlines()])
    if r["sessions"]:
        out.extend(["", "sessions (%d):" % len(r["sessions"])])
        for s in r["sessions"][:8]:
            out.append("  %5s  %s%s" % (human_age(now - s["last_ts"]), s["title"],
                                        "  [headless]" if s.get("headless") else ""))
    if r.get("errors"):
        out.extend(["", "errors:"] + ["  " + e for e in r["errors"]])
    return "\n".join(out)


def host_preview_text(host, caches, rows, now):
    mine = [r for r in rows if r["host"] == host]
    out = ["%s: %d project%s" % (host, len(mine), "" if len(mine) == 1 else "s")]
    for c in caches:
        if c["host"] == host:
            out.append(sync_age(c, now))
            if c.get("stale_since"):
                out.append("⚠ last sync failed: %s" % c.get("last_error"))
    for t in ("active", "stale", "abandoned", "deleted"):
        n = sum(1 for r in mine if r["tier"] == t)
        if n:
            out.append("  %s %s: %d" % (TIER_ICON[t], t, n))
    return "\n".join(out)


def pick_project(rows, now, notice=None, select=None):
    """select: (host, path) to put the cursor on, e.g. the project opened last."""
    header = PROJECT_HEADER + ("\n" + notice if notice else "")
    lines = project_lines(rows, now)
    extra = []
    if select:
        want = SEP.join(select) + SEP
        pos = next((i for i, ln in enumerate(lines, 1) if ln.startswith(want)), None)
        if pos:
            extra.append("--bind=load:pos(%d)" % pos)
    # --no-sort keeps the host grouping while filtering
    key, sel = run_fzf(lines, extra + [
                               "--delimiter=\t", "--with-nth=3..", "--no-sort", "--layout=reverse",
                               "--header=" + header, "--preview=" + preview_cmd(),
                               "--preview-window=right,50%,wrap"], expect=PROJECT_KEYS)
    if not sel:
        raise Cancelled()
    host, path, _ = sel[0].split(SEP, 2)
    return key, host, path


def pick_session(pairs, now):
    lines = [session_line(r, s, now) for r, s in pairs]
    _, sel = run_fzf(lines, ["--delimiter=\t", "--with-nth=4..", "--tiebreak=index",
                             "--header=Enter: resume in a new tab",
                             "--preview=" + preview_cmd(), "--preview-window=right,40%,wrap"])
    if not sel:
        raise Cancelled()
    host, path, sid, _ = sel[0].split(SEP, 3)
    return host, path, sid


def pick_prompt(items, now, query=""):
    lines = [prompt_line(r, s, t, now) for r, s, t in items]
    args = ["--delimiter=\t", "--with-nth=5..", "--exact", "--tiebreak=index",
            "--header=Enter: resume in a new tab · 'word: fuzzy",
            "--preview=" + spreview_cmd(), "--preview-window=right,45%,wrap"]
    if query:
        args.append("--query=" + query)
    _, sel = run_fzf(lines, args)
    if not sel:
        raise Cancelled()
    host, path, sid, _ = sel[0].split(SEP, 3)
    return host, path, sid


def pick_files(files, preview=None):
    files = sorted(files, key=lambda f: (not f["suspicious"], f["path"]))
    k = sum(1 for f in files if f["suspicious"])
    args = ["--multi", "--layout=reverse", "--delimiter=\t", "--with-nth=2..",
            "--header=Tab: toggle · Enter: confirm (⚑ = matches junk rules, preselected)"]
    if preview:
        args += ["--preview=" + preview, "--preview-window=right,50%,wrap"]
    if k:
        # `load` fires once piped input is read; `start` can fire before items exist.
        args.append("--bind=load:" + "+".join(["toggle+down"] * k) + "+first")
    _, sel = run_fzf([file_line(f) for f in files], args)
    return [line.split(SEP, 1)[0] for line in sel]


def pick_line(lines, header):
    _, sel = run_fzf(lines, ["--header=" + header, "--layout=reverse"])
    if not sel:
        raise Cancelled()
    return sel[0]


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
