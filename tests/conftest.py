import os
import subprocess

import pytest

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}


def run_git(path, *args):
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", "-C", str(path)] + list(args), check=True, env=env,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def write(path, content="x"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w") as f:
        f.write(content)


@pytest.fixture
def make_repo():
    def _make(path, files=None, commit=True):
        path = str(path)
        os.makedirs(path, exist_ok=True)
        run_git(path, "init", "-q", "-b", "main")
        for rel, content in (files or {"README.md": "hi"}).items():
            write(os.path.join(path, rel), content)
        if commit:
            run_git(path, "add", "-A")
            run_git(path, "commit", "-q", "-m", "init")
        return path
    return _make
