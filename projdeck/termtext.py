"""Untrusted text made safe for a terminal. One implementation, used by every terminal outlet
(ui, board's status-line document, report, cli): no ESC sequences, no C0 / DEL / C1 codes."""
import re

_ESC_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]"              # CSI
                     r"|\x1b\][^\x07\x1b\x9c\n]*(?:\x07|\x1b\\|\x9c)?"  # OSC: BEL / ST / C1 ST, stops at EOL
                     r"|\x1b[ -/]*[0-~]?")                      # other ESC sequences
_CTRL_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# bidi controls and zero-width characters that can disguise a name (U+200C / U+200D stay:
# emoji and several scripts need them)
_INVISIBLE_RE = re.compile("[\u061c\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def has_surrogates(text):
    """True when text holds a lone surrogate (non-UTF-8 bytes read with surrogateescape)."""
    return bool(_SURROGATE_RE.search(text))


def no_surrogates(text):
    """text with every lone surrogate (what surrogateescape makes of non-UTF-8 bytes)
    replaced by U+FFFD, so it can be encoded as UTF-8 at an outlet."""
    return _SURROGATE_RE.sub("\ufffd", text)


def safe(text):
    """Untrusted text made printable: no ESC sequences, no C0 / DEL / C1 characters (a newline
    stays, a tab becomes a space), no lone surrogates and no bidi / zero-width characters. Every session / backlog / prompt text goes through this."""
    text = _ESC_RE.sub("", no_surrogates(str(text if text is not None else "")))
    return _INVISIBLE_RE.sub("", _CTRL_RE.sub("", text.replace("\t", " ")))


def one_line(text):
    """safe(), on one line with runs of whitespace collapsed."""
    return " ".join(safe(text).split())


def has_control(text):
    """True when cleaning would change the text (ESC, control characters, surrogates, bidi / zero-width characters, tab or newline):
    a command built from it would not name the real thing."""
    text = str(text)
    return safe(text) != text or "\n" in text
