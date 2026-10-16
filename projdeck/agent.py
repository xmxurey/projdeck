#!/usr/bin/env python3
"""projdeck agent: runs on every host and answers one JSON request.

Standalone on purpose (stdlib only, Python >= 3.8, no projdeck imports),
because it is piped to remote hosts:
    ssh HOST python3 - '<json request>' < agent.py
"""
import fnmatch
import json
import os
import re
import shutil
import socket
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
GIT_TIMEOUT = 5
TRASH_DIR = "~/.proj-trash"
HOME_SKIP = {"Library", "Applications", "Movies", "Music", "Pictures", "Public",
             "Desktop", "Documents", "Downloads"}


def git(path, *args):
    r = subprocess.run(["git", "-C", path] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=GIT_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "git failed")
    return r.stdout.decode("utf-8", "replace")


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


def git_info(path):
    out = git(path, "status", "--porcelain=v1", "-b", "-z", "--untracked-files=all")
    info, untracked = parse_status(out)
    try:
        info["last_commit_ts"] = int(git(path, "log", "-1", "--format=%ct").strip() or 0)
    except RuntimeError:
        info["last_commit_ts"] = 0  # no commits yet
    return info, untracked


def is_suspicious(relpath, patterns):
    parts = relpath.rstrip("/").split("/")
    if fnmatch.fnmatchcase(parts[-1], "test_*.py") and TEST_DIRS & set(parts[:-1]):
        return False
    return any(fnmatch.fnmatchcase(part, pat) for part in parts for pat in patterns)


def last_mtime(path, max_files=MAX_FILES):
    newest = 0.0
    seen = 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
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
    if os.path.isfile(pf):
        with open(pf, encoding="utf-8", errors="replace") as f:
            lines = [line.rstrip() for _, line in zip(range(10), f)]
        return "\n".join(line for line in lines if line.strip())
    if repo:
        try:
            return git(path, "log", "-3", "--format=%s").strip()
        except RuntimeError:
            return ""
    return ""


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


def one_line(s, n=80):
    return " ".join(s.split())[:n]


def parse_session(fp):
    """Summarise one Claude Code transcript. The JSONL format is internal and
    changes between versions, so every field is optional and bad lines are skipped."""
    sid = os.path.basename(fp)[:-len(".jsonl")]
    cwd = entry = custom = ai = first = None
    with open(fp, encoding="utf-8", errors="replace") as f:
        for line in f:
            if cwd is None:
                m = CWD_RE.search(line)
                if m:
                    try:
                        cwd = json.loads('"%s"' % m.group(1))
                    except ValueError:
                        pass
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
            elif first is None and '"type":"user"' in line:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                text = _text_of(d.get("message")) if d.get("type") == "user" else ""
                if text.strip() and not text.lstrip().startswith("<"):
                    first = text
    if cwd is None:
        return None
    st = os.stat(fp)
    title = custom or ai or (one_line(first) if first else "") or "(untitled)"
    return {"id": sid, "cwd": cwd, "title": one_line(title), "last_ts": int(st.st_mtime),
            "size_kb": st.st_size // 1024, "headless": entry == "sdk-cli"}


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


def build_project(path, patterns):
    p = {"path": path, "exists": os.path.isdir(path), "git": None, "last_mtime": 0,
         "suspicious": [], "progress": "", "sessions": [], "errors": []}
    if not p["exists"]:
        return p
    repo = is_repo(path)
    try:
        p["last_mtime"] = last_mtime(path)
    except Exception as e:
        p["errors"].append("mtime: %s" % e)
    if repo:
        try:
            info, untracked = git_info(path)
            p["git"] = info
            p["suspicious"] = [u for u in untracked if is_suspicious(u, patterns)]
        except Exception as e:
            p["errors"].append("git: %s" % e)
    try:
        p["progress"] = progress(path, repo)
    except Exception as e:
        p["errors"].append("progress: %s" % e)
    return p


def collect(req):
    patterns = req.get("patterns") or DEFAULT_PATTERNS
    paths = discover(req.get("roots") or ["~"], int(req.get("scan_depth", 2)),
                     bool(req.get("include_plain", True)))
    sessions = scan_sessions(req.get("claude_dir") or "~/.claude")
    discovered = list(paths)
    for s in sessions:
        if owner(s["cwd"], discovered) is None:
            root = project_root_for(s["cwd"])  # known only through its sessions
            if root not in paths:
                paths.append(root)
    projects = {path: build_project(path, patterns) for path in paths}
    for s in sessions:
        projects[owner(s["cwd"], list(projects))]["sessions"].append(s)
    for p in projects.values():
        p["sessions"].sort(key=lambda s: -s["last_ts"])
    return {"schema": SCHEMA,
            "host": req.get("host") or socket.gethostname().split(".")[0],
            "collected_at": int(time.time()),
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
    _, untracked = git_info(path)
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
    current = set(git_info(path)[1])  # re-check: only still-untracked, non-ignored files
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


def act_restore(req):
    batch_id = req["batch"]
    _check_batch_id(batch_id)
    batch_dir = os.path.join(_trash_root(req), batch_id)
    with open(os.path.join(batch_dir, "manifest.json")) as f:
        manifest = json.load(f)
    items = manifest["items"]
    restored, skipped, kept = [], [], []
    for i, it in enumerate(items):
        if os.path.lexists(it["src"]) or not os.path.lexists(it["dst"]):
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


ACTIONS = {"collect": collect, "list": act_list, "trash": act_trash,
           "batches": act_batches, "restore": act_restore, "purge": act_purge}


def main(argv):
    try:
        req = json.loads(argv[1]) if len(argv) > 1 else {}
        result = ACTIONS[req.get("action", "collect")](req)
    except Exception as e:
        result = {"error": "%s: %s" % (type(e).__name__, e)}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 1 if isinstance(result, dict) and "error" in result else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
