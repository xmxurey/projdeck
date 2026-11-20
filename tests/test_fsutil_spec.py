"""T16 (spec): atomic writes go through fsutil; files 0600, new directories 0700."""
import os
import stat

import pytest

from projdeck import agent, fsutil, opener, serve, sync


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture
def umask002():
    old = os.umask(0o002)
    yield
    os.umask(old)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    return tmp_path / "cache"


def plant_before_excl(monkeypatch, planter):
    """On the first O_EXCL os.open, run planter(path) just before the real call."""
    real = os.open
    done = []

    def spy(path, flags, *a, **k):
        if flags & os.O_EXCL and not done:
            done.append(path)
            planter(os.fspath(path))
        return real(path, flags, *a, **k)
    monkeypatch.setattr(os, "open", spy)
    return done


# ---- 1. permissions ----

def test_new_file_is_0600_under_umask_002(tmp_path, umask002):
    p = str(tmp_path / "f.txt")
    fsutil.write_atomic(p, "hello")
    assert open(p).read() == "hello" and mode(p) == 0o600


def test_explicit_mode_is_honoured(tmp_path, umask002):
    p = str(tmp_path / "f.txt")
    fsutil.write_atomic(p, "x", 0o640)
    assert mode(p) == 0o640


def test_explicit_mode_survives_a_stricter_umask(tmp_path):
    """fchmod sets the requested mode exactly: the umask only narrows what O_CREAT creates."""
    old = os.umask(0o077)
    try:
        p = str(tmp_path / "f.txt")
        fsutil.write_atomic(p, "x", 0o640)
    finally:
        os.umask(old)
    assert mode(p) == 0o640


def test_overwriting_a_0644_file_leaves_0600(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("old")
    os.chmod(str(p), 0o644)
    fsutil.write_atomic(str(p), "new")
    assert p.read_text() == "new" and mode(str(p)) == 0o600


def test_every_new_directory_level_is_0700(tmp_path, umask002):
    p = str(tmp_path / "a" / "b" / "c" / "f.txt")
    fsutil.write_atomic(p, "x")
    for d in ("a", "a/b", "a/b/c"):
        assert mode(str(tmp_path / d)) == 0o700, d
    assert mode(p) == 0o600


def test_existing_directory_keeps_its_mode(tmp_path, umask002):
    d = tmp_path / "shared"
    d.mkdir()
    os.chmod(str(d), 0o775)
    fsutil.write_atomic(str(d / "sub" / "f.txt"), "x")
    assert mode(str(d)) == 0o775
    assert mode(str(d / "sub")) == 0o700


def test_overwrite_replaces_content_and_keeps_0600(tmp_path, umask002):
    p = str(tmp_path / "f.txt")
    fsutil.write_atomic(p, "one")
    fsutil.write_atomic(p, "two")
    assert open(p).read() == "two" and mode(p) == 0o600
    assert os.listdir(str(tmp_path)) == ["f.txt"]


def test_empty_text_is_written(tmp_path):
    p = str(tmp_path / "e")
    fsutil.write_atomic(p, "")
    assert os.path.getsize(p) == 0


def test_surrogateescape_text_round_trips_as_bytes(tmp_path):
    p = str(tmp_path / "s")
    fsutil.write_atomic(p, "a\udcffb")
    assert open(p, "rb").read() == b"a\xffb"


# ---- 2. no following of planted links / stale temp files ----

def test_planted_symlink_at_temp_name_is_not_followed(tmp_path, monkeypatch):
    target = tmp_path / "victim"
    target.write_text("precious")
    p = str(tmp_path / "f.txt")
    done = plant_before_excl(monkeypatch, lambda tmp: os.symlink(str(target), tmp))
    fsutil.write_atomic(p, "new")
    assert done, "temp file was not created with O_EXCL"
    assert target.read_text() == "precious"
    assert open(p).read() == "new" and not os.path.islink(p)
    assert sorted(os.listdir(str(tmp_path))) == ["f.txt", "victim"]


def test_dangling_symlink_at_temp_name_is_not_followed(tmp_path, monkeypatch):
    target = tmp_path / "never-created"
    p = str(tmp_path / "f.txt")
    plant_before_excl(monkeypatch, lambda tmp: os.symlink(str(target), tmp))
    fsutil.write_atomic(p, "new")
    assert not target.exists()
    assert open(p).read() == "new"


def test_stale_temp_file_is_removed_and_write_succeeds(tmp_path, monkeypatch, umask002):
    p = str(tmp_path / "f.txt")

    def stale(tmp):
        with open(tmp, "w") as f:
            f.write("leftover")
        os.chmod(tmp, 0o666)
    plant_before_excl(monkeypatch, stale)
    fsutil.write_atomic(p, "new")
    assert open(p).read() == "new" and mode(p) == 0o600
    assert os.listdir(str(tmp_path)) == ["f.txt"]


def test_target_symlink_is_replaced_not_written_through(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("precious")
    p = tmp_path / "f.txt"
    os.symlink(str(victim), str(p))
    fsutil.write_atomic(str(p), "new")
    assert victim.read_text() == "precious"
    assert p.read_text() == "new" and not os.path.islink(str(p))


# ---- 3. failure in the middle ----

@pytest.mark.parametrize("exc", [KeyboardInterrupt, OSError, RuntimeError])
def test_interrupted_write_leaves_old_file_and_no_temp(tmp_path, monkeypatch, exc):
    p = str(tmp_path / "f.txt")
    fsutil.write_atomic(p, "old")

    def boom(*a, **k):
        raise exc("stop")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(exc):
        fsutil.write_atomic(p, "new content")
    monkeypatch.undo()
    assert open(p).read() == "old"
    assert os.listdir(str(tmp_path)) == ["f.txt"]


def test_interrupted_first_write_leaves_nothing(tmp_path, monkeypatch):
    p = str(tmp_path / "f.txt")
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        fsutil.write_atomic(p, "new")
    monkeypatch.undo()
    assert os.listdir(str(tmp_path)) == []


# ---- 4. append_line ----

def test_append_line_creates_0600_and_appends(tmp_path, umask002):
    p = str(tmp_path / "u.jsonl")
    fsutil.append_line(p, '{"a": 1}\n')
    fsutil.append_line(p, '{"b": 2}\n')
    assert open(p).read() == '{"a": 1}\n{"b": 2}\n'
    assert mode(p) == 0o600


def test_append_line_tightens_existing_0664_file(tmp_path):
    p = tmp_path / "u.jsonl"
    p.write_text("old\n")
    os.chmod(str(p), 0o664)
    fsutil.append_line(str(p), "new\n")
    assert p.read_text() == "old\nnew\n" and mode(str(p)) == 0o600


@pytest.mark.parametrize("text", ["café\n", "中\n", "ok \udcff\n", "x\x80"])
def test_append_line_rejects_non_ascii_and_writes_nothing(tmp_path, text):
    p = tmp_path / "u.jsonl"
    p.write_text("old\n")
    with pytest.raises(ValueError):
        fsutil.append_line(str(p), text)
    assert p.read_text() == "old\n"


def test_append_line_rejects_non_ascii_without_creating_content(tmp_path):
    p = tmp_path / "new.jsonl"
    with pytest.raises(ValueError):
        fsutil.append_line(str(p), "é")
    assert not p.exists() or p.read_text() == ""


def test_append_line_accepts_ascii_edge(tmp_path):
    p = str(tmp_path / "a")
    fsutil.append_line(p, "\x7f~\n")
    assert open(p, "rb").read() == b"\x7f~\n"


def test_append_line_creates_new_parent_dirs_if_needed_or_fails_cleanly(tmp_path):
    p = str(tmp_path / "d" / "u")
    try:
        fsutil.append_line(p, "x\n")
    except OSError:
        return
    assert mode(str(tmp_path / "d")) == 0o700


# ---- alias ----

def test_sync_write_atomic_is_the_fsutil_function():
    assert sync._write_atomic is fsutil.write_atomic


# ---- real writers: cache, live, statusline, layout ----

def test_cache_file_is_0600_and_new_cache_dir_0700(cache, umask002):
    sync._write_cache("s-host", {"schema": agent.SCHEMA, "host": "s-host", "synced_at": 1,
                                 "projects": []})
    p = sync._cache_path("s-host")
    assert mode(p) == 0o600
    assert mode(str(cache)) == 0o700


def test_live_cache_file_is_0600(cache, umask002):
    p = sync._live_path("s-host")
    sync._write_json(p, {"schema": agent.SCHEMA, "host": "s-host", "sessions": []})
    assert mode(p) == 0o600
    assert mode(os.path.dirname(p)) == 0o700


def test_record_writes_0600(cache, umask002):
    p = str(cache / "r.json")
    stub = {"schema": agent.SCHEMA, "collected_at": 0}
    sync._record(p, lambda: {"schema": agent.SCHEMA, "host": "s-host", "collected_at": 5,
                             "projects": []}, stub, "s-host", 100)
    assert os.path.exists(p) and mode(p) == 0o600


def test_record_failure_stub_writes_0600(cache, umask002):
    p = str(cache / "r.json")

    def fetch():
        raise ValueError("nope")
    sync._record(p, fetch, {"schema": agent.SCHEMA, "collected_at": 0}, "s-host", 100)
    assert mode(p) == 0o600


def test_statusline_file_is_0600(cache, umask002):
    p = serve.statusline_path()
    sync._write_json(p, {"schema": 1, "waiting": 0})
    assert mode(p) == 0o600
    assert mode(os.path.dirname(p)) == 0o700


def test_overwriting_loose_cache_file_tightens_it(cache):
    p = sync._cache_path("s-host")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("{}")
    os.chmod(p, 0o666)
    sync._write_cache("s-host", {"schema": agent.SCHEMA, "host": "s-host", "synced_at": 1,
                                 "projects": []})
    assert mode(p) == 0o600


def test_zellij_layout_is_0600_and_new_layout_dir_0700(tmp_path, umask002):
    proj = tmp_path / "quant"
    proj.mkdir()
    ldir = tmp_path / "layouts"
    cmd = opener.zellij_new_session(str(proj), "proj-quant", str(ldir), False)
    assert "--new-session-with-layout" in cmd
    kdl = ldir / "proj-quant.kdl"
    assert kdl.exists() and mode(str(kdl)) == 0o600
    assert mode(str(ldir)) == 0o700


def test_zellij_layout_existing_dir_not_chmodded(tmp_path, umask002):
    ldir = tmp_path / "layouts"
    ldir.mkdir()
    os.chmod(str(ldir), 0o775)
    opener.zellij_new_session(str(tmp_path), "proj-x", str(ldir), False)
    assert mode(str(ldir)) == 0o775
    assert mode(str(ldir / "proj-x.kdl")) == 0o600
