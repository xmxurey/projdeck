"""fzf-based pickers and the preview pane."""
import os
import shlex
import subprocess
import sys
import time

from projdeck.model import TIER_ICON, human_age, n_suspicious

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


def file_preview_cmd(host_name, root):
    return "%s -m projdeck _fpreview %s %s {1}" % (shlex.quote(sys.executable),
                                                   shlex.quote(host_name), shlex.quote(root))


def _name(path):
    return os.path.basename(path.rstrip("/")) or path


def project_line(r, now):
    g = r.get("git") or {}
    flags = []
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
    display = "%-8s %s %-28s %5s  %s" % (r["host"][:8], TIER_ICON[r["tier"]], _name(r["path"])[:28],
                                         human_age(now - r["activity"]), " ".join(flags))
    return SEP.join([r["host"], r["path"], display])


def session_line(r, s, now):
    display = "%-8s %5s  %-20s %s%s" % (r["host"][:8], human_age(now - s["last_ts"]),
                                        _name(r["path"])[:20], s["title"],
                                        "  [headless]" if s.get("headless") else "")
    return SEP.join([r["host"], r["path"], s["id"], display])


def file_line(f):
    return SEP.join([f["path"], "%s %s  (%dK)" % ("⚑" if f["suspicious"] else " ", f["path"],
                                                   f["size"] // 1024)])


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


def pick_project(rows, now):
    lines = [project_line(r, now) for r in rows]
    key, sel = run_fzf(lines, ["--delimiter=\t", "--with-nth=3..", "--tiebreak=index",
                               "--header=" + PROJECT_HEADER, "--preview=" + preview_cmd(),
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
