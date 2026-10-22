"""Markdown overview: what needs attention across all hosts."""
import time

from projdeck.model import human_age, n_suspicious, sync_age


def _key(r):
    return "`%s:%s`" % (r["host"], r["path"])


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
            k = "`%s:%s`" % (e.get("host"), e.get("path"))
            per[k] = per.get(k, 0) + 1
    top = sorted(per.items(), key=lambda kv: -kv[1])[:5]
    if top:
        out.append("- most opened: " + ", ".join("%s ×%d" % kv for kv in top))
    return out


def render(rows, caches, now, events=None):
    out = ["# Projects overview (%s)" % time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
           "", "## Data freshness", ""]
    for c in caches:
        line = "- %s: %s" % (c["host"], sync_age(c, now))
        if c.get("stale_since"):
            line += " ⚠ last sync failed: %s" % c.get("last_error")
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
        "- %s %d files: %s" % (_key(r), n_suspicious(r), ", ".join(r["suspicious"][:5]))
        for r in susp[:15]])
    section("Abandoned", ["- %s %s" % (_key(r), human_age(now - r["activity"]))
                          for r in rows if r["tier"] == "abandoned"])
    section("Deleted (sessions point to missing dirs)",
            ["- %s" % _key(r) for r in rows if r["tier"] == "deleted"])
    if events is not None:
        out.extend(["", "## Your usage, last %d days" % USAGE_DAYS, ""] + _usage(events))
    return "\n".join(out) + "\n"
