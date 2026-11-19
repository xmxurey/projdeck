"""Markdown overview: what needs attention across all hosts."""
import re
import time

from projdeck import sync
from projdeck.board import UNSAFE_NAME  # noqa: F401 (also shown by the web page)
from projdeck.model import human_age, n_suspicious, sync_age
from projdeck.termtext import has_control, safe


def _key(r):
    return "`%s:%s`" % (_plain(r["host"]), _plain(r["path"]))


USAGE_DAYS = 14


def _usage(events):
    if not events:
        return ["- no activity logged yet"]
    counts = {}
    for e in events:
        counts[e.get("action")] = counts.get(e.get("action"), 0) + 1
    out = ["- " + " · ".join("%s %d" % (a, counts[a]) for a in
                             ("open", "resume", "clean", "restore") if a in counts)]
    per = {}
    for e in events:
        if e.get("action") in ("open", "resume"):
            k = "`%s:%s`" % (_plain(e.get("host")), _plain(e.get("path")))
            per[k] = per.get(k, 0) + 1
    top = sorted(per.items(), key=lambda kv: -kv[1])[:5]
    if top:
        out.append("- most opened: " + ", ".join("%s ×%d" % kv for kv in top))
    return out


def _code(text):
    """Inline markdown code; a fence one backtick longer than the longest run inside keeps a
    backtick in the text (a zellij session name can have one) from ending the span."""
    runs = re.findall(r"`+", text)
    fence = "`" * (max(map(len, runs)) + 1 if runs else 1)
    return "%s %s %s" % (fence, text, fence) if runs else "`%s`" % text


PROJ_NOTE = "（proj 下次在该项目按 Enter 时会自动删除）"


def _plain(text):
    return safe(text).replace("\n", " ").replace("`", "'")


def _exited_section(board, lives, cfg, now):
    """Exited zellij sessions, with the age of each zellij host's live data. Only hosts whose
    zellij is collected (sync.zellij_in_scope) are listed. No live data is not the same as none:
    such a host is named as unknown, and when every one is unknown the count is (?)."""
    hosts = [h for h in (cfg or {}).get("hosts") or () if sync.zellij_in_scope(h)]
    by = {(lv or {}).get("host"): lv for lv in lives or ()}
    data = {h["name"]: by.get(h["name"]) or {} for h in hosts}
    known = {n for n, lv in data.items() if lv.get("zellij_exited") is not None}
    exited = [e for e in (((board or {}).get("summary") or {}).get("zellij_exited") or [])
              if e.get("host") in known]  # only hosts that were checked; no data, no command
    if not hosts:
        return ["", "## Exited zellij sessions (0)", "", "- none (no zellij hosts)"]
    lines = []
    for h in hosts:
        at = data[h["name"]].get("collected_at")
        lines.append("- %s: %s" % (_plain(h["name"]),
                                   "live data %s ago" % human_age(now - at) if at else "no live data"))
    for e in exited:
        name, cmd, host = e.get("name"), e.get("cmd"), _plain(e.get("host"))
        if name is None or not cmd or has_control(name):
            lines.append("- `%s`: %s" % (host, UNSAFE_NAME))
            continue
        line = "- `%s`: %s" % (host, _code(safe(cmd)))
        if str(name).startswith("proj-"):
            line += " " + PROJ_NOTE
        lines.append(line)
    for h in hosts:
        if h["name"] not in known:
            lines.append("- `%s`: unknown (no live data)" % _plain(h["name"]))
    if known and not exited:
        lines.append("- none")
    return ["", "## Exited zellij sessions (%s)" % (len(exited) if known else "?"), ""] + lines


def render(rows, caches, now, events=None, board=None, lives=None, cfg=None):
    out = ["# Projects overview (%s)" % time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
           "", "## Data freshness", ""]
    for c in caches:
        line = "- %s: %s" % (_plain(c["host"]), sync_age(c, now))
        if c.get("stale_since"):
            line += " ⚠ last sync failed: %s" % _plain(c.get("last_error"))
        out.append(line)

    def section(title, items):
        out.extend(["", "## %s (%d)" % (title, len(items)), ""])
        out.extend(items or ["- none"])

    git_rows = [r for r in rows if r.get("git")]
    section("Uncommitted / unpushed", [
        "- %s ±%d ↑%d" % (_key(r), r["git"]["dirty"], r["git"]["ahead"])
        for r in git_rows if r["git"]["dirty"] or r["git"]["ahead"]])
    section("No upstream branch", ["- %s" % _key(r) for r in git_rows if r["git"]["no_upstream"]])
    susp = sorted((r for r in rows if n_suspicious(r)), key=lambda r: -n_suspicious(r))
    section("Suspicious untracked files", [
        "- %s %d files: %s" % (_key(r), n_suspicious(r), ", ".join(_plain(f) for f in r["suspicious"][:5]))
        for r in susp[:15]])
    section("Abandoned", ["- %s %s" % (_key(r), human_age(now - r["activity"]))
                          for r in rows if r["tier"] == "abandoned"])
    section("Deleted (sessions point to missing dirs)",
            ["- %s" % _key(r) for r in rows if r["tier"] == "deleted"])
    out.extend(_exited_section(board, lives, cfg, now))
    if events is not None:
        out.extend(["", "## Your usage, last %d days" % USAGE_DAYS, ""] + _usage(events))
    return "\n".join(out) + "\n"
