"""Untrusted text made safe for a terminal. One implementation, used by every terminal outlet
(ui, board's status-line document, report, cli): no ESC sequences, no C0 / DEL / C1 codes."""
import re

_ESC_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]"              # CSI
                     r"|\x1b\][^\x07\x1b\x9c\n]*(?:\x07|\x1b\\|\x9c)?"  # OSC: BEL / ST / C1 ST, stops at EOL
                     r"|\x1b[ -/]*[0-~]?")                      # other ESC sequences
_CTRL_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe(text):
    """Untrusted text made printable: no ESC sequences, no C0 / DEL / C1 characters (a newline
    stays, a tab becomes a space). Every session / backlog / prompt text goes through this."""
    text = _ESC_RE.sub("", str(text if text is not None else ""))
    return _CTRL_RE.sub("", text.replace("\t", " "))


def one_line(text):
    """safe(), on one line with runs of whitespace collapsed."""
    return " ".join(safe(text).split())


def has_control(text):
    """True when cleaning would change the text (ESC, control characters, tab or newline):
    a command built from it would not name the real thing."""
    text = str(text)
    return safe(text) != text or "\n" in text
