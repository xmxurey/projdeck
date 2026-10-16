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


def discover(roots, depth, include_plain=True):
    """Git repos under roots (not descending into repos) plus, optionally,
    non-git direct children of a root that contain no repo."""
    found = []

    def walk(d, level):
        if is_repo(d):
            found.append(d)
            return True
        hit = False
        if level < depth:
            for sub in _subdirs(d):
                hit = walk(sub, level + 1) or hit
        if level == 1 and not hit and include_plain:
            found.append(d)
        return hit

    for r in roots:
        r = os.path.abspath(os.path.expanduser(r))
        if os.path.isdir(r):
            walk(r, 0)
    return sorted(set(found))
