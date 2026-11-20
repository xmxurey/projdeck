#!/usr/bin/env python3
"""projdeck agent: runs on every host and answers one JSON request.

Standalone on purpose (stdlib only, Python >= 3.8, no projdeck imports),
because it is piped to remote hosts:
    ssh HOST python3 - '<json request>' < agent.py
"""
import calendar
import datetime
import fnmatch
import json
import os
import re
from collections import deque
import shlex
import shutil
import socket
import stat as statmod
import subprocess
import sys
import time

SCHEMA = 1
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "target", "dist", "build",
             "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", ".next"}
DEFAULT_PATTERNS = ["test_*.py", "*_backup*", "*.bak", "*.log", "*SUMMARY*.md",
                    "*IMPLEMENTATION*.md", "*REPORT*.md", "crontab-backup*",
                    "tmp*", "scratch*", "*.orig", "*_v2.*", "*_old.*"]
TEST_DIRS = {"tests", "test"}
MAX_FILES = 5000
SUSPICIOUS_CAP = 200
PROMPTS_KEEP = 200  # per session, for `proj grep`
GIT_TIMEOUT = 5
TRASH_DIR = "~/.proj-trash"
HOME_SKIP = {"Library", "Applications", "Movies", "Music", "Pictures", "Public",
             "Desktop", "Documents", "Downloads"}


# A scanned repo's own .git/config is untrusted: keep it from running commands
# (fsmonitor hook) and from writing to the index on a read-only status.
GIT_SAFE_CONFIG = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]


def git(path, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(["git"] + GIT_SAFE_CONFIG + ["-C", path] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=GIT_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError("git timed out after %ds" % GIT_TIMEOUT)
    except OSError as e:
        raise RuntimeError("cannot run git: %s" % e)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "git failed")
    return r.stdout.decode("utf-8", "surrogateescape")


def is_repo(path):
    return os.path.exists(os.path.join(path, ".git"))


def parse_status(out):
    """Parse `git status --porcelain=v1 -b -z` into (info, untracked_paths)."""
    entries = out.split("\0")
    info = {"branch": None, "dirty": 0, "untracked": 0, "ahead": 0, "no_upstream": True}
    untracked = []
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if not e:
            continue
        if e.startswith("## "):
            head = e[3:]
            if head.startswith("No commits yet on "):
                info["branch"] = head[len("No commits yet on "):]
                continue
            name, sep, rest = head.partition("...")
            info["branch"] = name.split(" ")[0]
            if sep:
                info["no_upstream"] = False
                m = re.search(r"ahead (\d+)", rest)
                if m:
                    info["ahead"] = int(m.group(1))
            continue
        xy, path = e[:2], e[3:]
        if xy == "??":
            untracked.append(path)
        else:
            info["dirty"] += 1
            if xy[0] in "RC":
                i += 1  # rename/copy: the next entry is the source path
    info["untracked"] = len(untracked)
    return info, untracked


COMMIT_TS_CAP = 2000


def _has_commits(path):
    """False only for 'no commits yet' (rev-parse -q exits 1 with empty stderr)."""
    try:
        git(path, "rev-parse", "-q", "--verify", "HEAD")
    except RuntimeError as e:
        if str(e) == "git failed":
            return False
        raise
    return True


def git_status(path):
    """(info, untracked_paths) from `git status` alone: what list / trash need."""
    out = git(path, "status", "--porcelain=v1", "-b", "-z", "--untracked-files=all")
    return parse_status(out)


def git_info(path):
    """git_status plus the commit facts collect shows; a failing git log raises."""
    info, untracked = git_status(path)
    if _has_commits(path):
        ts, _, subject = git(path, "log", "-1", "--format=%ct%x00%s").partition("\0")
        info["last_commit_ts"] = int(ts.strip() or 0)
        info["last_commit_subject"] = subject.strip()
        stamps = [int(x) for x in git(path, "log", "--since=14.days", "--format=%ct").split()]
    else:  # a repo with no commits yet; any other failure propagates as a git error
        info["last_commit_ts"], info["last_commit_subject"], stamps = 0, "", []
    info["commit_ts_14d"] = stamps[:COMMIT_TS_CAP]
    return info, untracked


BACKLOG_PATH = "docs/backlog.md"
BACKLOG_TITLE_MAX = 160
BACKLOG_TEXT_MAX = 2048
BACKLOG_ITEMS_MAX = 300
BACKLOG_RAW_MAX = 64 * 1024
_ITEM_RE = re.compile(r"(?:-|\d+\.)[ \t]+(.*)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _clip_bytes(text, limit):
    b = text.encode("utf-8")
    return text if len(b) <= limit else b[:limit].decode("utf-8", "ignore")


def parse_backlog(text):
    """Items of a backlog.md: unindented `- ` / `N. ` lines and `###` headings;
    `raw` carries the original text only when it is non-empty but has no items."""
    items = []
    section = None
    cur = None  # (head line, section, [lines])

    def flush():
        if cur is None:
            return
        head, sec, lines = cur
        while lines and not lines[-1].strip():
            lines.pop()
        body = "\n".join(lines)
        m = _BOLD_RE.search(head)
        title = (m.group(1) if m else head).strip()
        items.append({"title": title[:BACKLOG_TITLE_MAX],
                      "done": "~~" in head or "\u2705" in body,
                      "section": sec, "text": _clip_bytes(body, BACKLOG_TEXT_MAX)})

    for line in text.splitlines():
        h = re.match(r"(#{1,6})[ \t]+(.*?)\s*#*\s*$", line)
        m = _ITEM_RE.match(line)
        if h or m:
            flush()
            cur = None
            if h and len(h.group(1)) == 2:
                section = h.group(2).strip()
            elif h and len(h.group(1)) == 3:
                head = h.group(2).strip()
                cur = (head, section, [line])
            elif m:
                cur = (m.group(1).strip(), section, [line])
        elif cur is not None:
            cur[2].append(line)
    flush()
    items = items[:BACKLOG_ITEMS_MAX]
    raw = text[:BACKLOG_RAW_MAX] if (not items and text.strip()) else None
    return {"items": items, "raw": raw}


UNTRUSTED_READ_MAX = 1024 * 1024  # bytes; files inside scanned projects


def _resolves_inside(path, root):
    real, top = os.path.realpath(path), os.path.realpath(root)
    return real == top or real.startswith(top.rstrip(os.sep) + os.sep)


def read_untrusted(path, root, limit=UNTRUSTED_READ_MAX, whole=False):
    """(text, mtime) of a file that lives in a scanned project. The resolved path must
    stay under root (no symlink out to ~/.ssh), it must be a regular file (no FIFO or
    /dev/zero), and at most `limit` bytes are read; whole=True raises ValueError for a
    longer file instead of truncating it. Other refusals raise OSError."""
    if not _resolves_inside(path, root):
        raise OSError("refusing %s: it resolves outside %s" % (path, root))
    fd = os.open(os.path.realpath(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not statmod.S_ISREG(st.st_mode):
            raise OSError("refusing %s: not a regular file" % path)
        chunks, left = [], limit + 1
        while left > 0:
            b = os.read(fd, min(left, 65536))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > limit:
        if whole:
            raise ValueError("%s is larger than %d bytes" % (path, limit))
        data = data[:limit]
    return data.decode("utf-8", "replace"), st.st_mtime


def read_backlog(project_path):
    f = os.path.join(project_path, BACKLOG_PATH)
    try:
        text, mtime = read_untrusted(f, project_path)
    except OSError:
        return None
    r = parse_backlog(text)
    done = sum(1 for i in r["items"] if i["done"])
    return {"path": BACKLOG_PATH, "mtime": int(mtime), "items": r["items"],
            "open": len(r["items"]) - done, "done": done, "raw": r["raw"]}


def is_suspicious(relpath, patterns):
    parts = relpath.rstrip("/").split("/")
    if fnmatch.fnmatchcase(parts[-1], "test_*.py") and TEST_DIRS & set(parts[:-1]):
        return False
    return any(fnmatch.fnmatchcase(part, pat) for part in parts for pat in patterns)


def last_mtime(path, max_files=MAX_FILES, skip_paths=()):
    """Newest file mtime under path; skip_paths (nested projects) are not counted."""
    newest = 0.0
    seen = 0
    skip_paths = set(skip_paths)
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and os.path.join(dirpath, d) not in skip_paths]
        for fn in filenames:
            try:
                newest = max(newest, os.lstat(os.path.join(dirpath, fn)).st_mtime)
            except OSError:
                pass
            seen += 1
            if seen >= max_files:
                return int(newest)
    return int(newest)


def progress(path, repo):
    pf = os.path.join(path, "PROGRESS.md")
    if os.path.lexists(pf):
        try:
            text = read_untrusted(pf, path, 64 * 1024)[0]
        except OSError:
            return ""
        lines = [line.rstrip() for line in text.splitlines()[:10]]
        return "\n".join(line for line in lines if line.strip())
    if repo:
        try:
            return git(path, "log", "-3", "--format=%s").strip()
        except RuntimeError:
            return ""
    return ""


REGISTRY = "PROJECTS.md"
REGISTRY_TAG_RE = re.compile(r"^##\s+(.+?)\s*$")
REGISTRY_ROW_RE = re.compile(r"^\|\s*(🚩\s*)?`([^`]+)`")  # 🚩 before the path: flagged


def read_registry(home):
    """~/PROJECTS.md: '## Tag' sections whose table rows start with `rel/path/`.
    Returns ({abs path: tag}, tags in file order, {flagged abs paths}); empty if missing."""
    tags, order, flags, tag = {}, [], set(), None
    try:
        with open(os.path.join(home, REGISTRY), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}, [], set()
    for line in lines:
        m = REGISTRY_TAG_RE.match(line)
        if m:
            tag = m.group(1)
            continue
        m = REGISTRY_ROW_RE.match(line)
        if m and tag:
            rel = m.group(2).strip().rstrip("/")
            path = os.path.normpath(os.path.join(home, os.path.expanduser(rel)))
            tags[path] = tag
            if m.group(1):
                flags.add(path)
            if tag not in order:
                order.append(tag)
    return tags, order, flags


def _subdirs(d):
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        sub = os.path.join(d, n)
        if not n.startswith(".") and n not in SKIP_DIRS and os.path.isdir(sub) \
                and not os.path.islink(sub):
            out.append(sub)
    return out


def _repo_root_or_raise(path):
    try:
        top = git(path, "rev-parse", "--show-toplevel").strip()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        raise ValueError("not a git repository: %s" % path)
    if os.path.realpath(path) != os.path.realpath(top):
        raise ValueError("clean works on a repository root; %s is inside %s" % (path, top))


def discover(roots, depth, include_plain=True):
    """Git repos under roots (not descending into repos) plus, optionally,
    non-git direct children of a root that contain no repo."""
    found = []

    def walk(d, level, skip=()):
        if is_repo(d):
            found.append(d)
            return True
        hit = False
        if level < depth:
            for sub in _subdirs(d):
                if os.path.basename(sub) in skip:
                    continue
                hit = walk(sub, level + 1) or hit
        if level == 1 and not hit and include_plain:
            found.append(d)
        return hit

    for r in roots:
        r = os.path.abspath(os.path.expanduser(r))
        if not os.path.isdir(r):
            continue
        if os.path.realpath(r) == os.path.realpath(os.path.expanduser("~")):
            skip = HOME_SKIP
        else:
            skip = ()
        walk(r, 0, skip)
    return sorted(set(found))


CWD_RE = re.compile(r'"cwd":"((?:[^"\\]|\\.)*)"')
ENTRY_RE = re.compile(r'"entrypoint":"([^"]*)"')


def _text_of(message):
    content = (message or {}).get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text") or ""
    return ""


def _is_prompt(text):
    t = text.lstrip()
    return bool(t) and not t.startswith("<") and not t.startswith("[Request interrupted")


def input_kind(d):
    """Classify a transcript line: "human" (typed by the user), "peer" (message handed
    back by a subagent or task), "meta" (automatic continuation) or None (not an input)."""
    if not isinstance(d, dict) or d.get("type") != "user" or d.get("isCompactSummary"):
        return None
    message = d.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    text = _text_of(message)
    origin = d.get("origin")
    kind = origin.get("kind") if isinstance(origin, dict) else None
    if kind in ("peer", "task-notification"):
        return "peer"
    t = text.lstrip()
    if t.startswith("Another Claude session sent a message") or "<agent-message" in t[:300]:
        return "peer"
    if kind == "human":
        return "human"
    if kind is None and not origin and not d.get("isMeta"):
        return "human"
    return "meta"


def _last_texts(raw_lines, kind, keep):
    """Parse buffered raw lines; return the last `keep` texts of messages of type `kind`."""
    out = []
    for line in raw_lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == kind:
            text = _text_of(d.get("message"))
            if kind != "user" or _is_prompt(text):
                if text.strip():
                    out.append(text)
    return out[-keep:]


def one_line(s, n=80):
    return " ".join(s.split())[:n]


def session_title(custom, ai, first):
    """A session's title: its custom title, else the AI title, else the first human prompt."""
    return one_line(custom or ai or (one_line(first) if first else "") or "(untitled)")


def _cwd_of(line):
    """The first "cwd" value on a raw transcript line, decoded; None when there is none."""
    m = CWD_RE.search(line)
    if not m:
        return None
    try:
        return json.loads('"%s"' % m.group(1))
    except ValueError:
        return None


def parse_session(fp):
    """Summarise one Claude Code transcript. The JSONL format is internal and
    changes between versions, so every field is optional and bad lines are skipped."""
    sid = os.path.basename(fp)[:-len(".jsonl")]
    cwd = entry = custom = ai = first = None
    # only user lines are JSON-parsed; tool output and replies stay raw (they are the bulk)
    prompts, replies = deque(maxlen=PROMPTS_KEEP), deque(maxlen=6)
    with open(fp, encoding="utf-8", errors="replace") as f:
        for line in f:
            if cwd is None:
                cwd = _cwd_of(line)
            if entry is None:
                m = ENTRY_RE.search(line)
                if m:
                    entry = m.group(1)
            if '"type":"custom-title"' in line or '"type":"ai-title"' in line:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "custom-title" and d.get("customTitle"):
                    custom = d["customTitle"]
                elif d.get("type") == "ai-title" and d.get("aiTitle"):
                    ai = d["aiTitle"]
            elif '"type":"user"' in line and '"tool_result"' not in line:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if input_kind(d) != "human":
                    continue
                text = _text_of(d.get("message"))
                if _is_prompt(text):
                    prompts.append(one_line(text, 300))
                    if first is None:
                        first = text
            elif '"type":"assistant"' in line and '"type":"text"' in line:
                replies.append(line)
    if cwd is None:
        return None
    st = os.stat(fp)
    return {"id": sid, "cwd": cwd, "title": session_title(custom, ai, first),
            "last_ts": int(st.st_mtime),
            "size_kb": st.st_size // 1024, "headless": entry == "sdk-cli",
            "recent": [p[:120] for p in list(prompts)[-3:]],
            "last_reply": one_line((_last_texts(replies, "assistant", 1) or [""])[0], 200),
            "prompts": list(prompts)}


TS_RE = re.compile(r'"timestamp":"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)')
TASK_TOOLS = ("TodoWrite", "TaskCreate", "TaskUpdate")


def _ts_of(line):
    """Whole-second UTC timestamp of a transcript line, or None."""
    m = TS_RE.search(line)
    if not m:
        return None
    try:
        return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))
    except (ValueError, OverflowError):
        return None


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text") or "" for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def transcript_events(fp, since):
    """Read a whole transcript into {"state", "events", "asks"}. `state` covers the whole
    file; `events` (ts, H|P|M|C) and `asks` (start, end) keep only since-86400 onward."""
    horizon = since - 86400
    cwd = entry = custom = ai = first = None
    last_reply = last_human = None
    last_kind = last_ts = last_c = None
    last_input_seq = -1
    open_calls = {}  # tool_use id -> (name, ts, input, seq)
    task_uses, task_by_id = [], {}
    events, asks = [], []
    with open(fp, encoding="utf-8", errors="replace") as f:
        for seq, line in enumerate(f):
            cwd = _cwd_of(line) or cwd  # the latest cwd (parse_session keeps the first)
            if entry is None:
                m = ENTRY_RE.search(line)
                if m:
                    entry = m.group(1)
            is_title = '"type":"custom-title"' in line or '"type":"ai-title"' in line
            if not (is_title or '"type":"user"' in line or '"type":"assistant"' in line
                    or "turn_duration" in line):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            typ = d.get("type")
            if typ == "custom-title" and d.get("customTitle"):
                custom = d["customTitle"]
            elif typ == "ai-title" and d.get("aiTitle"):
                ai = d["aiTitle"]
            if first is None and typ == "user" and input_kind(d) == "human":
                # as parse_session: the first human prompt, with or without a timestamp
                t = _text_of(d.get("message"))
                if _is_prompt(t):
                    first = t
            ts = _ts_of(line)
            if ts is None:
                continue
            if typ == "system" and d.get("subtype") != "turn_duration":
                continue
            if typ not in ("user", "assistant", "system"):
                continue
            message = d.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            kind = input_kind(d) if typ == "user" else "C"
            if kind is None:  # tool_result line
                kind = "C"
                for b in content if isinstance(content, list) else ():
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    call = open_calls.pop(b.get("tool_use_id"), None)
                    if call and call[0] == "AskUserQuestion":
                        if call[1] >= horizon:
                            asks.append((call[1], ts))
                    tu = task_by_id.get(b.get("tool_use_id"))
                    if tu is not None:
                        tu["result_text"] = _result_text(b.get("content"))
                        tu["result_obj"] = d.get("toolUseResult")
            if typ == "user" and kind in ("H", "P", "M", "human", "peer", "meta"):
                kind = {"human": "H", "peer": "P", "meta": "M"}.get(kind, kind)
                last_kind, last_ts, last_input_seq = kind, ts, seq
                if kind == "H":
                    text = _text_of(message)
                    if _is_prompt(text):
                        last_human = {"ts": ts, "text": one_line(text, 300)}
            elif kind == "C":
                last_c = ts
            if typ == "assistant" and isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and (b.get("text") or "").strip():
                        last_reply = b["text"].strip()[-300:]
                    elif b.get("type") == "tool_use" and b.get("id"):
                        inp = b.get("input")
                        open_calls[b["id"]] = (b.get("name"), ts, inp, seq)
                        if b.get("name") in TASK_TOOLS:
                            tu = {"name": b["name"], "ts": ts, "input": inp,
                                  "result_text": None, "result_obj": None}
                            task_uses.append(tu)
                            task_by_id[b["id"]] = tu
            if ts >= horizon:
                events.append((ts, kind))
    pending = None
    for name, ts, inp, seq in open_calls.values():
        if seq <= last_input_seq:
            continue
        # latest call wins; calls from one assistant line tie, and AskUserQuestion wins the tie
        rank = (ts, seq, name == "AskUserQuestion")
        if pending is None or rank >= pending[4]:
            pending = (name, ts, inp, seq, rank)
    state = {"latest_cwd": cwd, "entry": entry,
             "title": session_title(custom, ai, first),
             "last_reply": last_reply or "", "last_human": last_human,
             "last_input_kind": last_kind, "last_input_ts": last_ts, "last_c_ts": last_c,
             "pending_tool": pending[0] if pending else None,
             "ask_question": None, "ask_started": None, "task_uses": task_uses}
    if pending and pending[0] == "AskUserQuestion":
        q = None
        inp = pending[2]
        try:
            q = inp["questions"][0]["question"]
        except (KeyError, IndexError, TypeError):
            pass
        if not isinstance(q, str) or not q:
            q = json.dumps(inp, ensure_ascii=False, separators=(",", ":"))
        state["ask_question"] = one_line(q, 300)
        state["ask_started"] = pending[1]
    return {"state": state, "events": events, "asks": asks}


def effective_status(session):
    """busy|idle, the one status both the card state and the timeline use."""
    if session.get("kind") == "background":
        return "busy" if session.get("state") in ("running", "working") else "idle"
    return "busy" if session.get("status") == "busy" else "idle"


SUB_FRESH = 600          # a subagent counts as running if its .jsonl changed within this many seconds
STALE_AFTER = 86400
BLOCKED_STATES = ("blocked", "needs_input", "waiting")
_FENCE_RE = re.compile(r"```.*?```", re.S)
_INLINE_RE = re.compile(r"`[^`\n]*`")
_URL_RE = re.compile(r"https?://\S+")


def _drop_orphan_fence(text):
    """last_reply keeps the tail of the reply, so an odd ``` count means the cut landed inside
    a block: the first ``` is the orphan, ignore it."""
    if text.count("```") % 2:
        i = text.find("```")
        text = text[:i] + text[i + 3:]
    return text


def _plain(text):
    """text without fenced code, inline code and URLs."""
    text = _FENCE_RE.sub("", text)
    text = _INLINE_RE.sub("", text)
    return _URL_RE.sub("", text)


def _trim_tail(text):
    return text.rstrip(" \t\r\n*\"'\u201d\u2019)\uff09]\u300d")


def last_paragraph(text):
    """(original, plain): the last paragraph that still has text once code and URLs are
    removed. `original` is that paragraph as written; `plain` is its stripped form."""
    text = _drop_orphan_fence(text or "")
    fences = []

    def hide(m):
        fences.append(m.group(0))
        return "\x00%d\x00" % (len(fences) - 1)

    hidden = _FENCE_RE.sub(hide, text)      # blank lines inside a fence do not split
    for para in reversed(re.split(r"\n\s*\n", hidden)):
        orig = re.sub("\x00(\\d+)\x00", lambda m: fences[int(m.group(1))], para).strip()
        plain = _trim_tail(_plain(orig))
        if plain.strip():
            return orig, plain
    return "", ""


def asks_you(text):
    """True when the last paragraph of text (code and URLs removed) holds a question mark."""
    plain = last_paragraph(text)[1]
    return "?" in plain or "\uff1f" in plain


def count_subagents(session_dir, now):
    """Subagents under <session_dir>/subagents that are unfinished and touched in the last 10 min."""
    sub = os.path.join(session_dir, "subagents")
    try:
        names = os.listdir(sub)
    except OSError:
        return 0
    n = 0
    for name in names:
        if not name.endswith(".meta.json"):
            continue
        try:
            with open(os.path.join(sub, name), encoding="utf-8", errors="replace") as f:
                meta = json.load(f)
            if isinstance(meta, dict) and meta.get("finished") is True:
                continue
            mt = os.stat(os.path.join(sub, name[:-len(".meta.json")] + ".jsonl")).st_mtime
        except (OSError, ValueError):
            continue
        if now - mt <= SUB_FRESH:
            n += 1
    return n


def _sub_span(path):
    """[first timestamp, mtime] of one subagent transcript, or None when unreadable,
    timestamp-less or inverted."""
    try:
        end = os.stat(path).st_mtime
        with open(path, "rb") as f:
            head = f.read(64 * 1024)
    except OSError:
        return None
    start = None
    for raw in head.splitlines():
        start = _ts_of(raw.decode("utf-8", "replace"))
        if start is not None:
            break
    if start is None or start > end:
        return None
    return [start, int(end)]


def subagent_spans(session_dir, memo=None, used=None):
    """[[start, end], ...] of the subagent transcripts under <session_dir>/subagents:
    first timestamp to last write. Results are kept in `memo` keyed by
    ("sub", path, size, mtime_ns); keys looked at are added to `used` so the caller can
    prune the rest."""
    sub = os.path.join(session_dir, "subagents")
    try:
        names = sorted(n for n in os.listdir(sub) if n.endswith(".jsonl"))
    except OSError:
        return []
    spans = []
    for name in names:
        path = os.path.join(sub, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = ("sub", path, st.st_size, st.st_mtime_ns)
        if memo is not None and key in memo:
            span = memo[key]
        else:
            span = _sub_span(path)
            if memo is not None:
                memo[key] = span
        if used is not None:
            used.add(key)
        if span:
            spans.append(list(span))
    return spans


def _secs(v):
    """Epoch seconds from a seconds/milliseconds number or an ISO 8601 string, else None."""
    if isinstance(v, str):
        m = re.match(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?$",
                     v.strip())
        if not m:
            return None
        t = calendar.timegm(tuple(int(x) for x in m.groups()[:6]) + (0, 0, 0))
        z = m.group(7)
        if z and z != "Z":
            off = int(z[1:3]) * 3600 + int(z[-2:]) * 60
            t -= off if z[0] == "+" else -off
        return t if t > 0 else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return None
    return int(v / 1000) if v > 1e11 else int(v)


def session_state(session, tx_state, now, subagents_running):
    """{"state", "question", "idle_since"} for a live session, first matching row of the table."""
    tx_state = tx_state or {}
    background = session.get("kind") == "background"
    pending = tx_state.get("pending_tool")
    ask = pending == "AskUserQuestion"
    reply = tx_state.get("last_reply") or ""
    question = None
    if ask:
        question = tx_state.get("ask_question")
    if not question and background:
        question = session.get("needs") or session.get("detail")
    if not question:
        question = last_paragraph(reply)[0]
    question = (question or "")[:300]
    idle_since = tx_state.get("ask_started") if ask else tx_state.get("last_c_ts")
    if not idle_since:
        idle_since = _secs(session.get("statusUpdatedAt"))
    out = {"state": "idle", "question": question, "idle_since": idle_since}
    last_active = max(_secs(session.get("mtime")) or 0, _secs(session.get("updatedAt")) or 0)
    if background and last_active and now - last_active > STALE_AFTER:
        out["state"] = "stale"
    elif ask:
        out["state"] = "waiting"
    elif effective_status(session) == "idle" and (
            asks_you(reply) or pending
            or (background and session.get("state") in BLOCKED_STATES)):
        out["state"] = "waiting"
    elif subagents_running > 0:
        out["state"] = "sub"
    elif effective_status(session) == "busy":
        out["state"] = "busy"
    return out


GAP_WAIT = 180    # idle gap / AskUserQuestion answer time before it counts as waiting
MERGE_GAP = 60
MAX_SEGMENTS = 400


def _turns(events):
    """[[start, end], ...] and, per turn, the kind that started it (None for a turn whose
    start predates the kept events)."""
    turns = []
    for ts, kind in sorted(events, key=lambda e: e[0]):
        if kind in ("H", "P", "M"):
            turns.append([ts, ts, kind])
        elif turns:
            turns[-1][1] = ts
        else:
            turns.append([ts, ts, None])
    return turns


def _split_gap(start, end, spans):
    """Parts of [start, end] covered by the union of spans -> ("sub", a, b); the uncovered
    parts longer than GAP_WAIT -> ("wait", a, b)."""
    out, pos = [], start
    for a, b in sorted((max(a, start), min(b, end)) for a, b in spans):
        if b <= a or b <= pos:
            continue
        if a > pos:
            out.append(["wait", pos, a])
        out.append(["sub", max(a, pos), b])
        pos = b
    if pos < end:
        out.append(["wait", pos, end])
    return [g for g in out if g[0] == "sub" or g[2] - g[1] > GAP_WAIT]


def segments(tx, now, since, live, status, subagents_running, sub_spans=()):
    """Timeline [[kind, start, end], ...] (integer seconds) clipped to [since, now].
    A gap before a P turn is "sub" only where sub_spans (subagent write periods) cover it."""
    state = tx.get("state") or {}
    turns = _turns(tx.get("events") or [])
    waits = [(a, b) for a, b in (tx.get("asks") or []) if b - a > GAP_WAIT]
    pending_ask = state.get("pending_tool") == "AskUserQuestion" and state.get("ask_started")
    segs = []
    for i, (start, end, _kind) in enumerate(turns):
        if pending_ask and i == len(turns) - 1:
            end = min(end, state["ask_started"])
        pieces = [[start, end]]
        for a, b in waits:
            nxt = []
            for s, e in pieces:
                if b <= s or a >= e:
                    nxt.append([s, e])
                    continue
                if a > s:
                    nxt.append([s, a])
                if b < e:
                    nxt.append([b, e])
            pieces = nxt
        segs += [["busy", s, e] for s, e in pieces]
        segs += [["wait", max(a, start), min(b, end)] for a, b in waits
                 if a < end and b > start]
        if i + 1 < len(turns):
            ts, nkind = turns[i + 1][0], turns[i + 1][2]
            if nkind == "P":
                segs += _split_gap(end, ts, sub_spans)
            elif nkind == "H" and ts - end > GAP_WAIT:
                segs.append(["wait", end, ts])
    if live:
        if pending_ask:
            segs.append(["wait_now", state["ask_started"], now])
        else:
            last = turns[-1][1] if turns else max(state.get("last_c_ts") or 0,
                                                  state.get("last_input_ts") or 0)
            if last:
                if status == "busy":
                    segs.append(["busy", last, now])
                elif subagents_running > 0:
                    segs.append(["sub", last, now])
                elif now - last > GAP_WAIT:
                    segs.append(["wait_now", last, now])
    clipped = []
    for kind, s, e in segs:
        s, e = max(int(s), since), min(int(e), now)
        if e > s:
            clipped.append([kind, s, e])
    clipped.sort(key=lambda g: (g[1], g[2]))
    merged = []
    for g in clipped:
        if merged and merged[-1][0] == g[0] and g[1] - merged[-1][2] <= MERGE_GAP:
            merged[-1][2] = max(merged[-1][2], g[2])
        else:
            merged.append(g)
    return merged[-MAX_SEGMENTS:]


def scan_sessions(claude_dir):
    base = os.path.join(os.path.expanduser(claude_dir), "projects")
    out = []
    try:
        dirs = sorted(os.listdir(base))
    except OSError:
        return out
    for d in dirs:
        pd = os.path.join(base, d)
        if not os.path.isdir(pd):
            continue
        try:
            fns = sorted(os.listdir(pd))
        except OSError:
            continue
        for fn in fns:
            if not fn.endswith(".jsonl"):
                continue
            try:
                s = parse_session(os.path.join(pd, fn))
            except OSError:
                s = None
            if s:
                out.append(s)
    return out


def owner(cwd, paths):
    best = None
    for p in paths:
        if cwd == p or cwd.startswith(p.rstrip("/") + "/"):
            if best is None or len(p) > len(best):
                best = p
    return best


def project_root_for(cwd):
    if os.path.isdir(cwd):
        try:
            return git(cwd, "rev-parse", "--show-toplevel").strip() or cwd
        except Exception:
            pass
    return cwd


def build_project(path, patterns, nested=()):
    p = {"path": path, "exists": os.path.isdir(path), "git": None, "last_mtime": 0,
         "suspicious": [], "progress": "", "sessions": [], "errors": [], "backlog": None}
    if not p["exists"]:
        return p
    repo = is_repo(path)
    try:
        # no files at all (empty dir): the directory's own mtime, not 1970
        p["last_mtime"] = last_mtime(path, skip_paths=nested) or int(os.stat(path).st_mtime)
    except Exception as e:
        p["errors"].append("mtime: %s" % e)
    if repo:
        try:
            info, untracked = git_info(path)
            p["git"] = info
            susp = [u for u in untracked if is_suspicious(u, patterns)]
            p["suspicious"], p["suspicious_count"] = susp[:SUSPICIOUS_CAP], len(susp)
        except Exception as e:
            p["errors"].append("git: %s" % e)
    try:
        p["progress"] = progress(path, repo)
    except Exception as e:
        p["errors"].append("progress: %s" % e)
    try:
        p["backlog"] = read_backlog(path)
    except Exception as e:
        p["backlog"] = None
        p["errors"].append("backlog: %s" % e)
    return p


def tmux_sessions():
    """(name, start dir) of live tmux sessions that proj opened; [] without tmux."""
    try:
        r = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}\t#{session_path}"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:  # e.g. no server running
        return []
    out = []
    for line in r.stdout.splitlines():
        name, _, path = line.partition("\t")
        if name.startswith("proj-") and path:
            out.append((name, path))
    return out


def proc_table(proc="/proc"):
    """{pid: (ppid, comm, argv, cwd)} for the processes we can read; {} without /proc."""
    out = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        d = os.path.join(proc, n)
        try:
            with open(os.path.join(d, "stat"), encoding="utf-8", errors="replace") as f:
                stat = f.read()
            with open(os.path.join(d, "cmdline"), "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue  # gone, or not ours
        # "pid (comm) state ppid ...": comm may contain spaces and parens
        head, _, rest = stat.rpartition(")")
        comm = head.partition("(")[2]
        fields = rest.split()
        if len(fields) < 2 or not fields[1].isdigit():
            continue
        try:
            cwd = os.readlink(os.path.join(d, "cwd"))
        except OSError:
            cwd = None
        out[int(n)] = (int(fields[1]), comm, argv, cwd)
    return out


def zellij_sessions(proc="/proc"):
    """[(name, sorted cwds of its descendants, number of claude processes)] for every
    live zellij session: one `zellij --server <dir>/<name>` process each. Exited
    sessions have no server, so they never show up."""
    table = proc_table(proc)
    children = {}
    for pid, (ppid, _, _, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    out = []
    for pid, (_, _, argv, _) in table.items():
        if (len(argv) < 3 or os.path.basename(argv[0]) != "zellij"
                or argv[1] != "--server"):
            continue
        cwds, n_claude, todo = set(), 0, list(children.get(pid, []))
        while todo:
            c = todo.pop()
            _, comm, _, cwd = table[c]
            if cwd:
                cwds.add(cwd)
            if comm == "claude":
                n_claude += 1
            todo.extend(children.get(c, []))
        out.append((os.path.basename(argv[2].rstrip("/")), sorted(cwds), n_claude))
    return out


def _claude_bin():
    return shutil.which("claude") or next(
        (c for c in [os.path.expanduser("~/.local/bin/claude")] if os.access(c, os.X_OK)), None)


def claude_agents(timeout=5):
    """Entries of `claude agents --json`; None when it is missing, slow, failing or not JSON."""
    exe = _claude_bin()
    if not exe:
        return None
    try:
        p = subprocess.Popen([exe, "agents", "--json"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             universal_newlines=True, start_new_session=True)
    except OSError:
        return None
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, 9)
        except OSError:
            pass
        try:
            p.communicate(timeout=1)  # a daemon that escaped the group may still hold stdout
        except subprocess.TimeoutExpired:
            pass
        return None
    if p.returncode != 0:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    if isinstance(data, dict):
        data = data.get("agents")
    if not isinstance(data, list):
        return None
    keep = ("id", "cwd", "kind", "startedAt", "sessionId", "name", "state", "status", "pid")
    return [{k: a[k] for k in keep if k in a} for a in data if isinstance(a, dict)]


def _proc_start_ok(pid, proc_start, proc):
    """True when /proc/<pid> exists and, if proc_start is known, is the same process."""
    try:
        with open(os.path.join(proc, str(pid), "stat"), encoding="utf-8", errors="replace") as f:
            stat = f.read()
    except (OSError, ValueError):
        return False
    if proc_start is None:
        return True
    fields = stat.rpartition(")")[2].split()  # fields[0] is column 3
    try:
        return int(fields[19]) == int(proc_start)
    except (IndexError, ValueError, TypeError):
        return False


def _session_files(claude_dir, proc):
    """[session dict from sessions/<pid>.json] whose process is still the same one."""
    base = os.path.join(os.path.expanduser(claude_dir), "sessions")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(base, n), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        pid = d.get("pid")
        if pid is None and n[:-5].isdigit():
            pid = int(n[:-5])
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        d["pid"] = pid
        if _proc_start_ok(pid, d.get("procStart"), proc):
            out.append(d)
    return out


def sessions_fallback(claude_dir, proc="/proc"):
    """Live sessions from ~/.claude/sessions/*.json; None without /proc."""
    if not os.path.isdir(proc):
        return None
    out = []
    for d in _session_files(claude_dir, proc):
        a = {"id": str(d.get("id") or d["pid"]), "kind": d.get("kind") or "interactive",
             "pid": d["pid"]}
        for k in ("cwd", "startedAt", "sessionId", "name", "status", "state", "statusUpdatedAt"):
            if k in d:
                a[k] = d[k]
        out.append(a)
    return out


def live_agents(claude_dir, proc="/proc"):
    """{"source", "agents"}: `claude agents` first, sessions files as fallback, one entry
    per sessionId (interactive wins), with statusUpdatedAt / detail / needs / updatedAt."""
    agents = claude_agents()
    source = "claude agents"
    if agents is None:
        agents = sessions_fallback(claude_dir, proc)
        source = "sessions files"
    if agents is None:
        return {"source": "none", "agents": []}
    chosen, order = {}, []
    for i, a in enumerate(agents):
        key = a.get("sessionId") or ("#%d" % i)
        if key not in chosen:
            order.append(key)
            chosen[key] = a
        elif a.get("kind") == "interactive" and chosen[key].get("kind") != "interactive":
            chosen[key] = a
    result = [dict(chosen[k]) for k in order]
    by_pid = None
    base = os.path.expanduser(claude_dir)
    for a in result:
        if a.get("kind") == "interactive":
            if by_pid is None:
                by_pid = {d["pid"]: d for d in _session_files(claude_dir, proc)}
            f = by_pid.get(a.get("pid"))
            if f and "statusUpdatedAt" in f:
                a["statusUpdatedAt"] = f["statusUpdatedAt"]
        elif a.get("kind") == "background" and a.get("id"):
            try:
                with open(os.path.join(base, "jobs", str(a["id"]), "state.json"),
                          encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(st, dict):
                for k in ("detail", "needs", "updatedAt"):
                    if st.get(k) is not None:
                        a[k] = st[k]
    return {"source": source, "agents": result}


def _tmux_panes():
    """{pane pid: session name} from `tmux list-panes -a`; {} without tmux."""
    try:
        r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_pid}\t#{session_name}"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    out = {}
    for line in r.stdout.splitlines():
        pid, _, name = line.strip().partition("\t")
        if pid.isdigit() and name:
            out[int(pid)] = name
    return out


def attach_map(proc="/proc"):
    """{claude pid: shell command that attaches to its zellij/tmux session, or ""}.
    Only builds text; nothing is run."""
    table = proc_table(proc)
    panes = None
    out = {}
    for pid, (_, comm, argv, _) in table.items():
        if comm != "claude" and not (argv and os.path.basename(argv[0]) == "claude"):
            continue
        cmd, cur, seen = "", pid, set()
        while cur in table and cur not in seen:
            seen.add(cur)
            _, _, cargv, _ = table[cur]
            if (len(cargv) >= 3 and os.path.basename(cargv[0]) == "zellij"
                    and cargv[1] == "--server"):
                sname = os.path.basename(cargv[2].rstrip("/"))
                if not sname.startswith("-"):  # would be read as an option: no command
                    cmd = "zellij attach %s" % shlex.quote(sname)
                break
            if panes is None:
                panes = _tmux_panes()
            if cur in panes:
                cmd = "tmux attach -t %s" % shlex.quote(panes[cur])
                break
            cur = table[cur][0]
        out[pid] = cmd
    return out


def zellij_by_project(paths, proc="/proc"):
    """{project path: [{"name", "claude"}]}: a session belongs to every project one of
    its processes works in (longest matching project wins, like session ownership)."""
    out = {}
    for name, cwds, n in sorted(zellij_sessions(proc)):
        owners = {owner(c, paths) or owner(os.path.realpath(c), paths) for c in cwds}
        for o in sorted(owners - {None}):
            out.setdefault(o, []).append({"name": name, "claude": n})
    return out


TASK_TITLE_MAX = 160
RUN_STATUS = {"done": "done", "in_progress": "doing", "review": "doing", "pending": "todo",
              "blocked": "stuck", "interrupted": "stuck"}
PLAN_TASK_RE = re.compile(r"^#{3}\s+Task\s+(\w+)\s*[:：]\s*(.*?)\s*$", re.M)
SDD_PLAN_RE = re.compile(r"^\s*(?:[-*]\s*)?\**(?:计划|[Pp]lan)\**\s*[:：]\s*\**(.+?)\**\s*$", re.M)
SDD_DONE_RE = re.compile(r"Task\s+(\w+)\s*[:：]\s*complete", re.I)
SDD_BOX_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.*?)\s*$", re.M)


LEDGER_READ_MAX = 16 * 1024 * 1024  # log.jsonl of a long run


def _read_text(path, root, limit=UNTRUSTED_READ_MAX):
    return read_untrusted(path, root, limit, whole=True)[0]


def _iso_ts(v):
    """Epoch seconds as int from an ISO string (any offset) or a number; None if unusable."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if v == v and abs(v) != float("inf") else None
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _run_ledger_tasks(checkout, errors):
    runs = os.path.join(checkout, "docs", "dev", "runs")
    try:
        run_id = _read_text(os.path.join(runs, ".active"), checkout).strip()
    except (OSError, ValueError):
        return None
    if not run_id or "/" in run_id or run_id in (".", ".."):
        return None
    sp = os.path.join(runs, run_id, "state.json")
    try:
        state = json.loads(_read_text(sp, checkout, LEDGER_READ_MAX))
        if not isinstance(state, dict) or not isinstance(state.get("tasks", []), list):
            raise ValueError("tasks is not a list")
    except (OSError, ValueError) as e:
        errors.append("run ledger %s: %s" % (sp, e))
        return None
    items = []
    for t in state.get("tasks") or []:
        if not isinstance(t, dict):
            errors.append("run ledger %s: task entry is not an object" % sp)
            continue
        raw = t.get("status")
        status = RUN_STATUS.get(raw)
        if status is None:
            errors.append("run ledger %s: unknown status %r for %s" % (sp, raw, t.get("id")))
            status = "todo"
        rnd = t.get("round")
        rnd = rnd if isinstance(rnd, int) and not isinstance(rnd, bool) else 0
        if status == "done":
            note = "一次通过" if rnd == 0 else "评审 %d 轮" % (rnd + 1)
        elif status == "doing":
            note = str(t.get("stage") or "")
        else:
            note = ""
        items.append({"id": str(t.get("id", "")), "title": str(t.get("title") or "")[:TASK_TITLE_MAX],
                      "status": status, "note": note})
    merges = []
    try:
        log = _read_text(os.path.join(runs, run_id, "log.jsonl"), checkout, LEDGER_READ_MAX)
    except (OSError, ValueError):
        log = ""
    for line in log.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if not (ev.get("event") == "merged" or (ev.get("event") == "decision" and ev.get("note") == "merged")):
            continue
        ts = _iso_ts(ev.get("ts"))
        tid = ev.get("task")
        if ts is None or tid in (None, ""):
            errors.append("run ledger log.jsonl: merged event dropped (task=%r ts=%r)" % (tid, ev.get("ts")))
            continue
        merges.append([str(tid), ts])
    cur = state.get("current_task")
    return {"source": "run", "label": str(state.get("run_id") or run_id) if items else "还没有计划",
            "path": sp, "checkout": checkout, "current": str(cur) if cur else None,
            "items": items, "merges": merges}


def _plan_items(text):
    return [(m.group(1), m.group(2)[:TASK_TITLE_MAX]) for m in PLAN_TASK_RE.finditer(text)]


def _sdd_tasks(checkout, since_ts, errors):
    pp = os.path.join(checkout, ".superpowers", "sdd", "progress.md")
    try:
        if os.stat(pp).st_mtime < since_ts:
            return None
        text = _read_text(pp, checkout)
    except (OSError, ValueError):
        return None
    plan = []
    m = SDD_PLAN_RE.search(text)
    if m:
        ref = m.group(1).strip().strip("`").strip()
        full = ref if os.path.isabs(ref) else os.path.join(checkout, ref)
        try:
            plan = _plan_items(_read_text(full, checkout))
        except (OSError, ValueError) as e:
            errors.append("sdd plan %s: %s" % (full, e))
    items = []
    if plan:
        complete = set(SDD_DONE_RE.findall(text))
        doing_set = False
        for tid, title in plan:
            if tid in complete:
                status = "done"
            elif not doing_set:
                status, doing_set = "doing", True
            else:
                status = "todo"
            items.append({"id": tid, "title": title, "status": status, "note": ""})
    else:
        for i, mm in enumerate(SDD_BOX_RE.finditer(text), 1):
            items.append({"id": str(i), "title": mm.group(2)[:TASK_TITLE_MAX],
                          "status": "done" if mm.group(1) in "xX" else "todo", "note": ""})
    if not items:
        return None
    cur = next((i["id"] for i in items if i["status"] == "doing"), None)
    return {"source": "sdd", "label": "sdd 进度", "path": pp, "checkout": checkout,
            "current": cur, "items": items, "merges": []}


def _plan_file_tasks(checkout, since_ts):
    d = os.path.join(checkout, "docs", "superpowers", "plans")
    try:
        names = [n for n in os.listdir(d) if n.endswith(".md")]
    except OSError:
        return None
    cands = []
    for n in names:
        p = os.path.join(d, n)
        try:
            mt = os.stat(p).st_mtime
        except OSError:
            continue
        if mt >= since_ts:
            cands.append((mt, n, p))
    for _, _, p in sorted(cands, reverse=True):
        try:
            tasks = _plan_items(_read_text(p, checkout))
        except (OSError, ValueError):
            continue
        if tasks:
            return {"source": "plan", "label": "计划（无进度记录）", "path": p, "checkout": checkout,
                    "current": None, "merges": [],
                    "items": [{"id": i, "title": t, "status": "todo", "note": ""} for i, t in tasks]}
    return None


_TASK_STATUS = {"completed": "done", "in_progress": "doing", "pending": "todo"}


def _task_title(*cands):
    for c in cands:
        if isinstance(c, str) and c.strip():
            return c.strip().splitlines()[0][:160]
    return ""


def session_task_list(task_uses, since, path, checkout):
    """Rebuild the task list Claude kept in one session from its TodoWrite / TaskCreate /
    TaskUpdate calls (task source 3). Returns None when there is nothing to show."""
    uses = [u for u in task_uses or [] if isinstance(u, dict)]
    if not uses:
        return None
    errors = []
    todo_list = None          # latest whole TodoWrite list
    last_name = None          # tool of the last call that was not skipped
    items = []                # Task* replay: dicts with id/title/status/active
    created = 0
    for u in uses:
        name, inp = u.get("name"), u.get("input")
        if name == "TodoWrite":
            todos = inp.get("todos") if isinstance(inp, dict) else None
            if not isinstance(todos, list):
                errors.append("TodoWrite: todos is not a list")
                continue
            todo_list = []
            for n, t in enumerate(todos, 1):
                t = t if isinstance(t, dict) else {}
                st = _TASK_STATUS.get(t.get("status"), "todo")
                todo_list.append({"id": str(n), "title": _task_title(t.get("content")),
                                  "status": st, "active": t.get("activeForm") or ""})
            last_name = name
        elif name == "TaskCreate":
            if not isinstance(inp, dict):
                errors.append("TaskCreate: input is not an object")
                continue
            created += 1
            ro = u.get("result_obj")
            tid = None
            if isinstance(ro, dict) and isinstance(ro.get("task"), dict) and ro["task"].get("id") is not None:
                tid = str(ro["task"]["id"])
            if tid is None:
                m = re.search(r"Task #(\d+)", u.get("result_text") or "")
                tid = m.group(1) if m else str(created)
            last_name = name
            items.append({"id": tid, "status": "todo", "active": inp.get("activeForm") or "",
                          "title": _task_title(inp.get("subject"), inp.get("description"))})
        elif name == "TaskUpdate":
            if not isinstance(inp, dict) or inp.get("taskId") is None:
                errors.append("TaskUpdate: input has no taskId")
                continue
            tid = str(inp["taskId"])
            cur = next((i for i in (items or []) if i["id"] == tid), None)
            if cur is None:
                errors.append("TaskUpdate: unknown taskId %s" % tid)
                continue
            last_name = name
            st = inp.get("status")
            if st == "deleted":
                items.remove(cur)
                continue
            if st is not None:
                if st in _TASK_STATUS:
                    cur["status"] = _TASK_STATUS[st]
                else:
                    errors.append("TaskUpdate: unknown status %r for task %s" % (st, tid))
            if isinstance(inp.get("subject"), str) and inp["subject"].strip():
                cur["title"] = _task_title(inp["subject"])
            if inp.get("activeForm"):
                cur["active"] = inp["activeForm"]
    final = todo_list if last_name == "TodoWrite" and todo_list is not None else items
    if not final and not errors:
        return None
    final = final or []
    last_ts = max([u.get("ts") or 0 for u in uses] or [0])
    if final and all(i["status"] == "done" for i in final) and last_ts < since:
        return None
    out = [{"id": i["id"], "title": i["title"], "status": i["status"],
            "note": i["active"] if i["status"] == "doing" else ""} for i in final]
    cur_id = next((i["id"] for i in out if i["status"] == "doing"), None)
    return {"source": "session", "label": "会话内任务清单", "path": path, "checkout": checkout,
            "current": cur_id, "items": out, "merges": [], "errors": errors}


def find_tasks(checkouts, since_ts, session_tasks=None, errors=None):
    """First task source by priority: run ledger, sdd, session task list, plan file.

    If `errors` is a list, every error collected (also session_tasks["errors"]) is extended into
    it whatever is returned, so a broken ledger with no other source is not silently lost.
    """
    out = errors
    errors = []
    dirs = []
    for c in checkouts or []:
        if c and c not in dirs and os.path.isdir(c):
            dirs.append(c)
    found = None
    for fn in (lambda c: _run_ledger_tasks(c, errors), lambda c: _sdd_tasks(c, since_ts, errors)):
        for c in dirs:
            found = fn(c)
            if found:
                break
        if found:
            break
    if not found and isinstance(session_tasks, dict) and session_tasks.get("items"):
        found = dict(session_tasks)
        found["source"] = "session"
        found.setdefault("label", "会话内任务清单")
        found.setdefault("path", "")
        found.setdefault("checkout", dirs[0] if dirs else "")
        found.setdefault("current", None)
        found["merges"] = list(found.get("merges") or [])
        errors = list(found.get("errors") or []) + errors
    elif isinstance(session_tasks, dict) and isinstance(session_tasks.get("errors"), list):
        errors = list(session_tasks["errors"]) + errors
    if not found:
        for c in dirs:
            found = _plan_file_tasks(c, since_ts)
            if found:
                break
    if out is not None:
        out.extend(errors)
    if not found:
        return None
    found["errors"] = errors
    return found


def collect(req):
    patterns = req.get("patterns") or DEFAULT_PATTERNS
    paths = discover(req.get("roots") or ["~"], int(req.get("scan_depth", 2)),
                     bool(req.get("include_plain", True)))
    discovered = list(paths)
    home = os.path.expanduser("~")
    homes = [home, os.path.realpath(home)]
    # noise: headless `claude -p` runs (cron jobs) and sessions in /tmp and other
    # throwaway dirs; keep interactive sessions under home or in a scanned project
    sessions = [s for s in scan_sessions(req.get("claude_dir") or "~/.claude")
                if not s["headless"]
                and (owner(s["cwd"], homes) or owner(s["cwd"], discovered))]
    def owner_of(cwd, candidates):  # a symlinked cwd belongs where it points
        return owner(cwd, candidates) or owner(os.path.realpath(cwd), candidates)

    for s in sessions:
        if owner_of(s["cwd"], discovered) is None:
            root = project_root_for(s["cwd"])  # known only through its sessions
            if root not in paths:
                paths.append(root)
    def nested(path):
        return [q for q in paths if q.startswith(path.rstrip("/") + "/")]
    projects = {path: build_project(path, patterns, nested(path)) for path in paths}
    for s in sessions:
        projects[owner_of(s["cwd"], list(projects))]["sessions"].append(s)
    for p in projects.values():
        p["sessions"].sort(key=lambda s: -s["last_ts"])
    registry, tag_order, flags = read_registry(home)
    for path, p in projects.items():
        p["tag"] = registry.get(path) or registry.get(os.path.realpath(path))
        p["flag"] = path in flags or os.path.realpath(path) in flags
    if registry:  # with a registry, show only what you work on: registered or used
        projects = {path: p for path, p in projects.items() if p["tag"] or p["sessions"]}
    for name, path in tmux_sessions():
        o = owner(path, list(projects))
        if o:
            projects[o].setdefault("live_tmux", []).append(name)
    for o, live in zellij_by_project(list(projects)).items():
        projects[o]["live_zellij"] = live
    return {"schema": SCHEMA,
            "host": req.get("host") or socket.gethostname().split(".")[0],
            "collected_at": int(time.time()), "home": home,
            "tags": tag_order,
            "projects": sorted(projects.values(), key=lambda p: p["path"])}


def _trash_root(req):
    return os.path.expanduser(req.get("trash_dir") or TRASH_DIR)


def _write_manifest(batch_dir, manifest):
    tmp = os.path.join(batch_dir, "manifest.json.tmp")
    with open(tmp, "w") as f:
        json.dump(manifest, f)
    os.replace(tmp, os.path.join(batch_dir, "manifest.json"))


def act_list(req):
    path = os.path.abspath(os.path.expanduser(req["path"]))
    _repo_root_or_raise(path)
    patterns = req.get("patterns") or DEFAULT_PATTERNS
    _, untracked = git_status(path)  # commit history plays no part in cleaning
    files = []
    for rel in untracked:
        try:
            size = os.lstat(os.path.join(path, rel)).st_size
        except OSError:
            size = 0
        files.append({"path": rel, "suspicious": is_suspicious(rel, patterns), "size": size})
    return {"path": path, "files": files}


def act_trash(req):
    path = os.path.abspath(os.path.expanduser(req["path"]))
    _repo_root_or_raise(path)
    now = req.get("now") or time.time()
    current = set(git_status(path)[1])  # re-check: only still-untracked, non-ignored files
    root = _trash_root(req)
    base_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + os.path.basename(path)
    batch_id, n = base_id, 1
    while os.path.exists(os.path.join(root, batch_id)):
        n += 1
        batch_id = "%s-%d" % (base_id, n)
    batch_dir = os.path.join(root, batch_id)
    manifest = {"created": int(now), "project": path, "items": []}
    skipped = []
    for rel in req.get("files", []):
        src = os.path.normpath(os.path.join(path, rel))
        if os.path.isabs(rel) or not src.startswith(path + os.sep) or rel not in current:
            skipped.append(rel)
            continue
        dst = os.path.join(batch_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        manifest["items"].append({"src": src, "dst": dst})
        _write_manifest(batch_dir, manifest)  # after every move, so a crash loses nothing
    moved = [it["src"] for it in manifest["items"]]
    return {"batch": batch_id if moved else None, "moved": moved, "skipped": skipped}


def _load_batches(root):
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        try:
            with open(os.path.join(root, name, "manifest.json")) as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        try:
            out.append({"id": name, "created": m["created"], "project": m["project"],
                        "count": len(m["items"])})
        except (KeyError, TypeError):
            continue  # unreadable manifest: skip this batch only
    return out


def act_batches(req):
    return {"batches": _load_batches(_trash_root(req))}


def _discard_batch(batch_dir):
    """Drop the manifest and any directories left empty. Never deletes files: a
    stray file (not listed in the manifest) keeps its directory and survives."""
    os.remove(os.path.join(batch_dir, "manifest.json"))
    for dirpath, _dirnames, _filenames in os.walk(batch_dir, topdown=False):
        if dirpath != batch_dir:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
    try:
        os.rmdir(batch_dir)
    except OSError:
        pass


def _check_batch_id(batch_id):
    if not isinstance(batch_id, str) or "/" in batch_id or batch_id in ("", ".", ".."):
        raise ValueError("bad batch id %r" % (batch_id,))


def _inside(p, root):
    p, root = os.path.normpath(p), os.path.normpath(root)
    return os.path.isabs(p) and p.startswith(root.rstrip(os.sep) + os.sep)


def act_restore(req):
    batch_id = req["batch"]
    _check_batch_id(batch_id)
    batch_dir = os.path.join(_trash_root(req), batch_id)
    with open(os.path.join(batch_dir, "manifest.json")) as f:
        manifest = json.load(f)
    items = manifest["items"]
    restored, skipped, kept = [], [], []
    for i, it in enumerate(items):
        # only move back what this tool could have written: dst inside the batch,
        # src inside the cleaned project (guards against a damaged or edited manifest)
        if (not _inside(it["dst"], batch_dir) or not _inside(it["src"], manifest["project"])
                or os.path.lexists(it["src"]) or not os.path.lexists(it["dst"])):
            skipped.append(it["src"])
            kept.append(it)
            continue
        os.makedirs(os.path.dirname(it["src"]), exist_ok=True)
        shutil.move(it["dst"], it["src"])
        restored.append(it["src"])
        # Manifest tracks progress: drop this item now, so a failure on a later move
        # leaves only the unrestored items listed.
        manifest["items"] = kept + items[i + 1:]
        if manifest["items"]:
            _write_manifest(batch_dir, manifest)
    if kept:
        manifest["items"] = kept
        _write_manifest(batch_dir, manifest)
    else:
        _discard_batch(batch_dir)
    return {"restored": restored, "skipped": skipped}


def act_purge(req):
    root = _trash_root(req)
    ids = req.get("ids")
    if ids is not None:
        for i in ids:
            _check_batch_id(i)
        wanted = set(ids)
        cutoff = None
    else:
        days = float(req["older_days"])
        if days < 0:
            raise ValueError("older_days must be >= 0")
        wanted = None
        cutoff = (req.get("now") or time.time()) - days * 86400
    purged = []
    for b in _load_batches(root):
        if (b["id"] in wanted) if wanted is not None else (b["created"] < cutoff):
            shutil.rmtree(os.path.join(root, b["id"]))
            purged.append(b["id"])
    return {"purged": purged}


_NO_SESSIONS_RE = re.compile(r"\bno\b.*\bsessions?\b", re.I)
_ZJ_NAME_RE = re.compile(r"^(.*?)\s+\[Created\b")
# The trailing "(EXITED - attach to ...)". ASCII only on purpose: opener.zellij_attach decides the
# same thing in awk on the host, which has no Unicode word characters or spaces;
# tests/test_exited_equivalence.py keeps the two in step.
_ZJ_EXITED_RE = re.compile(r"\(EXITED(?![A-Za-z0-9_])[^()]*\)[ \t\r\f\v]*$")


def zellij_session_list(run=subprocess.run, timeout=5):
    """[{"name", "exited"}] for every zellij session, live and exited; [] when zellij says
    there are none; None when zellij is missing, slow or fails. Only runs `list-sessions`."""
    try:
        r = run(["zellij", "list-sessions", "-n"], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                universal_newlines=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    out, err = r.stdout or "", r.stderr or ""
    sessions = []
    for line in out.split("\n"):  # not splitlines(): awk reads lines at "\n" only
        line = line.strip(" \t\r\f\v")  # ASCII blanks only, like the awk in opener.zellij_attach
        if not line or _NO_SESSIONS_RE.search(line) and "[Created" not in line:
            continue
        m = _ZJ_NAME_RE.match(line)
        name = m.group(1) if m else line.split()[0]
        rest = line[m.end():] if m else line[len(name):]  # never the name: proj-EXITED is live
        sessions.append({"name": name, "exited": bool(_ZJ_EXITED_RE.search(rest))})
    if r.returncode != 0:
        return [] if not sessions and _NO_SESSIONS_RE.search(out + "\n" + err) else None
    return sessions


def _transcript_index(claude_dir):
    """{sessionId: transcript path} from one listdir per project directory."""
    base = os.path.join(claude_dir, "projects")
    index = {}
    try:
        dirs = sorted(os.listdir(base))
    except OSError:
        return index
    for d in dirs:
        pd = os.path.join(base, d)
        try:
            names = sorted(os.listdir(pd))
        except OSError:
            continue
        for fn in names:
            if fn.endswith(".jsonl"):
                index.setdefault(fn[:-len(".jsonl")], os.path.join(pd, fn))
    return index


def _commit_info(path, since):
    """{"last_commit": {"ts", "subject"} | None, "commits": [ts]} of one project. An empty
    repo or a non-git directory has no commits; a git failure gives commits None + "error"."""
    info = {"last_commit": None, "commits": []}
    if not is_repo(path):
        return info
    try:
        if not _has_commits(path):
            return info
        ts, _, subject = git(path, "log", "-1", "--format=%ct%x00%s").partition("\0")
        info["last_commit"] = {"ts": int(ts.strip() or 0), "subject": subject.strip()}
        info["commits"] = [int(x) for x in git(path, "log", "--since=%d" % since,
                                               "--format=%ct").split()][:COMMIT_TS_CAP]
    except (RuntimeError, ValueError) as e:
        return {"last_commit": None, "commits": None, "error": "git: %s" % e}
    return info


_INPUT_NAMES = {"H": "human", "P": "peer", "M": "meta"}


def act_live(req, memo=None):
    """One host's live view: sessions (live ones always, ended ones touched since `since`),
    their timelines, waiting state, task lists and attach commands. Read-only."""
    now = int(req.get("now") or time.time())
    since = int(req.get("since") or now - 86400)
    claude_dir = os.path.expanduser(req.get("claude_dir") or "~/.claude")
    paths = list(req.get("paths") or [])
    home = os.path.expanduser("~")
    homes = [home, os.path.realpath(home)]
    top_errors = []
    try:
        found = live_agents(claude_dir)
    except Exception as e:
        found = {"source": "none", "agents": []}
        top_errors.append("live agents: %s" % e)
    index = _transcript_index(claude_dir)
    try:
        attach = attach_map() if found["agents"] else {}
    except Exception as e:
        attach = {}
        top_errors.append("attach: %s" % e)
    used, cands = set(), []
    live_ids = set()
    for ag in found["agents"]:
        sid = ag.get("sessionId")
        if sid:
            live_ids.add(sid)
            cands.append((sid, ag))
        else:  # no sessionId to key on: keep it out, the board cannot place it
            continue
    for sid, fp in index.items():
        if sid in live_ids:
            continue
        try:
            if os.stat(fp).st_mtime >= since:
                cands.append((sid, None))
        except OSError:
            continue
    sessions, owners_in = [], {}
    root_cache = {}
    for sid, ag in cands:
        errors = []
        fp = index.get(sid)
        tx, mtime, read_failed = {"state": {}, "events": [], "asks": []}, None, None
        if fp:
            try:
                st = os.stat(fp)
                mtime = int(st.st_mtime)
                key = (fp, st.st_size, st.st_mtime_ns)
                if memo is not None and key in memo:
                    tx = memo[key]
                else:
                    tx = transcript_events(fp, since)
                    if memo is not None:
                        memo[key] = tx
                if memo is not None:
                    used.add(key)
            except Exception as e:
                errors.append("transcript %s: %s" % (fp, e))
                if not isinstance(e, FileNotFoundError):
                    read_failed = "transcript %s: %s" % (fp, e)
        tstate = tx.get("state") or {}
        if tstate.get("entry") == "sdk-cli":
            continue
        cwd = (ag or {}).get("cwd") or tstate.get("latest_cwd")
        if not cwd:
            if read_failed and ag is None:  # an ended session vanishing with its error
                top_errors.append(read_failed)
            continue
        if not (owner(cwd, homes) or owner(cwd, paths)):
            continue
        latest = tstate.get("latest_cwd") or cwd
        try:
            project = (owner(cwd, paths) or owner(os.path.realpath(cwd), paths)
                       or root_cache.setdefault(cwd, project_root_for(cwd)))
            is_live = ag is not None
            srec = dict(ag) if ag else {"kind": "interactive", "status": "idle"}
            srec["mtime"] = mtime
            status = effective_status(srec)
            subs = count_subagents(os.path.join(os.path.dirname(fp), sid), now) \
                if fp and is_live else 0
            if is_live:
                st = session_state(srec, tstate, now, subs)
            else:
                st = {"state": "ended", "question": "", "idle_since": tstate.get("last_c_ts")}
            evs = tx.get("events") or []
            started = _secs((ag or {}).get("startedAt")) or (min(e[0] for e in evs) if evs
                                                             else mtime)
            spans = []
            if fp and any(e[1] == "P" for e in evs):
                spans = subagent_spans(os.path.join(os.path.dirname(fp), sid), memo, used)
            segs = segments(tx, now, since, is_live, status, subs, spans)
            last_human = tstate.get("last_human")
            s = {"id": sid, "project": project, "cwd": cwd, "latest_cwd": latest,
                 "kind": srec.get("kind") or "interactive", "live": is_live,
                 "name": (ag or {}).get("name") or "", "title": tstate.get("title") or "",
                 "started_at": started, "status": status, "state": st["state"],
                 "idle_since": st["idle_since"], "question": st["question"],
                 "last_reply": (tstate.get("last_reply") or "")[-300:],
                 "last_human": last_human or {"ts": None, "text": ""},
                 "last_input": _INPUT_NAMES.get(tstate.get("last_input_kind")),
                 "subagents_running": subs, "segments": segs,
                 "attach": attach.get((ag or {}).get("pid")) or "",
                 "tasks": None, "tasks_owner": None, "errors": errors, "_mtime": mtime or 0}
            try:
                if latest not in root_cache:
                    root_cache[latest] = project_root_for(latest)
                own_list = session_task_list(tstate.get("task_uses"), since, fp or "", latest)
                s["_found"] = find_tasks([root_cache[latest], project], started or since,
                                         own_list, errors)
            except Exception as e:
                errors.append("tasks: %s" % e)
        except Exception as e:
            errors.append("session %s: %s" % (sid, e))
            s = {"id": sid, "project": owner(cwd, paths) or cwd, "cwd": cwd, "latest_cwd": latest,
                 "kind": "interactive", "live": ag is not None, "name": "", "title": "",
                 "started_at": None, "status": "idle", "state": "idle" if ag else "ended",
                 "idle_since": None, "question": "", "last_reply": "",
                 "last_human": {"ts": None, "text": ""}, "last_input": None,
                 "subagents_running": 0, "segments": [], "attach": "", "tasks": None,
                 "tasks_owner": None, "errors": errors, "_mtime": mtime or 0}
        sessions.append(s)
    # task ownership: one non-session source (type + path) hangs on one session
    for s in sessions:
        t = s.pop("_found", None)
        if not t:
            continue
        if t.get("source") == "session":
            s["tasks"] = t
        else:
            owners_in.setdefault((t.get("source"), t.get("path")), []).append((s, t))
    for group in owners_in.values():
        alive = [c for c in group if c[0]["live"]]
        if alive:
            own = min(alive, key=lambda c: (c[0]["started_at"] or 0, c[0]["id"]))
        else:
            own = max(group, key=lambda c: (c[0]["_mtime"], c[0]["id"]))
        for s, t in group:
            if s is own[0]:
                s["tasks"] = t
            else:
                s["tasks_owner"] = own[0]["id"]
    for s in sessions:
        s.pop("_mtime", None)
    if memo is not None:
        for k in [k for k in memo if k not in used]:
            del memo[k]
    projects = {}
    for s in sessions:
        if s["project"] not in projects:
            projects[s["project"]] = _commit_info(s["project"], since)
            if projects[s["project"]].get("error"):
                top_errors.append("%s: %s" % (s["project"], projects[s["project"]]["error"]))
    zellij = None
    if req.get("zellij"):
        listed = zellij_session_list()
        if listed is not None:
            zellij = sorted(z["name"] for z in listed if z["exited"])
    return {"schema": SCHEMA, "host": req.get("host") or socket.gethostname().split(".")[0],
            "collected_at": now, "source": found["source"], "since": since,
            "sessions": sessions, "projects": projects, "zellij_exited": zellij,
            "errors": top_errors}


ACTIONS = {"collect": collect, "live": act_live, "list": act_list, "trash": act_trash,
           "batches": act_batches, "restore": act_restore, "purge": act_purge}


def main(argv):
    try:
        req = json.loads(argv[1]) if len(argv) > 1 else {}
        action = req.get("action", "collect")
        if action not in ACTIONS:
            raise ValueError("unknown action %r" % (action,))
        result = ACTIONS[action](req)
    except Exception as e:
        result = {"error": "%s: %s" % (type(e).__name__, e)}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 1 if isinstance(result, dict) and "error" in result else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
