"""Markdown overview: what needs attention across all hosts."""
import time

from projdeck.model import human_age, n_suspicious


def _key(r):
    return "`%s:%s`" % (r["host"], r["path"])


def render(rows, caches, now):
    out = ["# Projects overview (%s)" % time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
           "", "## Data freshness", ""]
    for c in caches:
        line = "- %s: synced %s ago" % (c["host"], human_age(now - (c.get("synced_at") or 0)))
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
    return "\n".join(out) + "\n"
