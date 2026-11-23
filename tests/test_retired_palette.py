"""G17: colours retired by D6 must not come back in shipped files (bugbook 08).

The status-line script kept the old waiting orange after the terminal moved to the
new palette (case 08-1): a second copy of one fact that nobody grepped for. This guard
scans what ships (projdeck/ minus the vendored webkit copy, and contrib/) for the
pre-D6 values. The list is written out by hand from commit aeb5dbb, not derived from
the current constants, so a reverted constant is caught too.

Old values that are still in use on purpose are not listed: #2F5D8A (link text,
--link), #F6CFA6 (--wait-now, a background only) and #C0392B (the "now" line stroke).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# web values replaced in D6 (app.css variables, web.py TASK_FILL)
RETIRED_HEX = ("8DB4DC", "E8954A", "2F7D5B", "D5D9E0")
# terminal RGB triples replaced in D6 (ui.CELL_RGB, WAIT/DOING/DONE/STUCK_RGB)
RETIRED_RGB = ((111, 155, 203), (169, 198, 228), (240, 163, 94), (201, 132, 70),
               (141, 180, 220), (47, 125, 91), (192, 57, 43))
SHIPPED_SUFFIXES = {".py", ".css", ".js", ".html", ".sh", ".json"}


def retired_hits(text):
    """Every retired colour found in text: '#rrggbb' in any case, or an RGB triple
    written as '(r, g, b)' or as an ANSI '38;2;r;g;b' run."""
    hits = []
    for h in RETIRED_HEX:
        if re.search(r"#%s\b" % h, text, re.I):
            hits.append("#" + h)
    for r, g, b in RETIRED_RGB:
        if re.search(r"(?<!\d)%d\s*[,;]\s*%d\s*[,;]\s*%d(?!\d)" % (r, g, b), text):
            hits.append("%d,%d,%d" % (r, g, b))
    return hits


def shipped_files():
    for top in ("projdeck", "contrib"):
        for p in sorted((ROOT / top).rglob("*")):
            if p.is_file() and p.suffix in SHIPPED_SUFFIXES and "vendor" not in p.parts:
                yield p


def test_no_retired_colour_in_shipped_files():
    found = {}
    for p in shipped_files():
        hits = retired_hits(p.read_text(encoding="utf-8", errors="replace"))
        if hits:
            found[str(p.relative_to(ROOT))] = hits
    assert found == {}


def test_scan_covers_the_status_line_script():
    # the file of case 08-1 must stay inside the scanned set
    assert ROOT / "contrib" / "statusline-waiting.sh" in set(shipped_files())


def test_scan_flags_every_written_form():
    assert retired_hits("--sub: #8db4dc;") == ["#8DB4DC"]
    assert retired_hits('fill="#D5D9E0"') == ["#D5D9E0"]
    assert retired_hits("WAIT_RGB = (240, 163, 94)") == ["240,163,94"]
    assert retired_hits(r'"\u001b[38;2;240;163;94m"') == ["240,163,94"]
    assert retired_hits("(192,57,43)") == ["192,57,43"]


def test_scan_stays_quiet_on_current_and_nearby_values():
    assert retired_hits("--busy: #2a78d6; --link: #2F5D8A; --wait-now: #F6CFA6;") == []
    assert retired_hits("WAIT_RGB = (217, 89, 38); x = (1240, 163, 94); y = 240;163;945") == []
