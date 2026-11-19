#!/usr/bin/env python3
"""Update or verify projdeck/vendor/webkit/, the vendored copy of ~/dev/webkit.

    python3 scripts/update_webkit.py [--from DIR] [--check]

Update (default): copy the files listed in DIR/MANIFEST into the vendor
directory and record the source commit and sha256 of each file in SOURCE.json.
Refuses (exit 2) when DIR is not a git repo or its work tree is dirty.

--check: change nothing; every vendored file must match SOURCE.json, and with
--from also the same-named file at the recorded commit in DIR. Exit 1 on any
mismatch.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "projdeck", "vendor", "webkit")
SOURCE = os.path.join(VENDOR, "SOURCE.json")
IGNORED = ("SOURCE.json", "__init__.py", "__pycache__")
DEFAULT_FROM = "~/dev/webkit"


class Refuse(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise Refuse("git %s failed in %s: %s" % (" ".join(args), repo, p.stderr.decode(errors="replace").strip()))
    return p.stdout


def read_manifest(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def load_source():
    with open(SOURCE, encoding="utf-8") as f:
        return json.load(f)


def display_path(path):
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def check(src):
    bad = []
    rec = load_source()
    recorded = rec.get("files")
    if not isinstance(recorded, dict) or not recorded:
        bad.append("SOURCE.json: 'files' is missing or empty")
        recorded = {}
    actual = sorted(n for n in os.listdir(VENDOR)
                    if n not in IGNORED and os.path.isfile(os.path.join(VENDOR, n)))
    for name in actual:
        if name not in recorded:
            bad.append("%s: not recorded in SOURCE.json (added or entry removed by hand?)" % name)
    for name, want in sorted(recorded.items()):
        p = os.path.join(VENDOR, name)
        if not os.path.isfile(p):
            bad.append("%s: missing" % name)
            continue
        with open(p, "rb") as f:
            if sha256(f.read()) != want:
                bad.append("%s: differs from SOURCE.json (edited by hand?)" % name)
    if src:
        commit = rec.get("commit")
        if commit:
            for name in sorted(set(recorded) | set(actual)):
                try:
                    blob = git(src, "show", "%s:%s" % (commit, name))
                except Refuse as e:
                    bad.append("%s: %s" % (name, e))
                    continue
                p = os.path.join(VENDOR, name)
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        if f.read() != blob:
                            bad.append("%s: differs from source commit %s" % (name, commit[:7]))
        else:
            print("note: SOURCE.json has no commit (seed), skipping source comparison")
    for b in bad:
        print("MISMATCH " + b)
    if not bad:
        print("ok")
    return 1 if bad else 0


def update(src):
    if git(src, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise Refuse("%s is not a git work tree" % src)
    if git(src, "status", "--porcelain").strip():
        raise Refuse("%s has uncommitted changes" % src)
    commit = git(src, "rev-parse", "HEAD").decode().strip()
    commit_date = git(src, "show", "-s", "--format=%cI", "HEAD").decode().strip()
    mpath = os.path.join(src, "MANIFEST")
    if not os.path.isfile(mpath):
        raise Refuse("%s has no MANIFEST" % src)
    with open(mpath, encoding="utf-8") as f:
        names = read_manifest(f.read())
    if not names:
        raise Refuse("MANIFEST is empty")
    contents = {}
    for n in names:
        if os.path.isabs(n) or ".." in n.split("/") or "/" in n:
            raise Refuse("bad MANIFEST entry: %s" % n)
        p = os.path.join(src, n)
        if not os.path.isfile(p):
            raise Refuse("MANIFEST lists %s but it does not exist" % n)
        with open(p, "rb") as f:
            contents[n] = f.read()
    try:
        old = load_source()
    except (OSError, ValueError):
        old = {}
    changed = []
    for n, data in contents.items():
        p = os.path.join(VENDOR, n)
        try:
            with open(p, "rb") as f:
                same = f.read() == data
        except OSError:
            same = False
        if not same:
            changed.append(n)
        with open(p, "wb") as f:
            f.write(data)
    rec = {
        "repo": display_path(src),
        "commit": commit,
        "commit_date": commit_date,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "files": {n: sha256(d) for n, d in contents.items()},
    }
    tmp = SOURCE + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
        f.write("\n")
    os.replace(tmp, SOURCE)
    print("%s -> %s" % (old.get("commit") or old.get("repo") or "none", commit))
    print("changed: %s" % (", ".join(changed) if changed else "(none)"))
    print("commit message: chore: update vendored webkit to %s" % commit[:7])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="src", help="webkit repo (default %s; --check without it only verifies hashes)" % DEFAULT_FROM)
    ap.add_argument("--check", action="store_true", help="verify only, change nothing")
    a = ap.parse_args(argv)
    src = os.path.abspath(os.path.expanduser(a.src)) if a.src else None
    try:
        if a.check:
            return check(src)
        return update(src or os.path.expanduser(DEFAULT_FROM))
    except Refuse as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
