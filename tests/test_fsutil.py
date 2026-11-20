import errno
import os
import stat
import threading

import pytest

from projdeck import fsutil, sync


@pytest.fixture
def umask002():
    old = os.umask(0o002)
    yield
    os.umask(old)


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_write_atomic_file_0600_and_new_dirs_0700(tmp_path, umask002):
    f = tmp_path / "a" / "b" / "x.json"
    fsutil.write_atomic(str(f), "hi")
    assert f.read_text() == "hi"
    assert mode(f) == 0o600
    assert mode(tmp_path / "a") == 0o700 and mode(tmp_path / "a" / "b") == 0o700


def test_existing_directory_mode_untouched(tmp_path, umask002):
    d = tmp_path / "d"
    d.mkdir()
    os.chmod(d, 0o775)
    fsutil.write_atomic(str(d / "x"), "hi")
    assert mode(d) == 0o775


def test_planted_symlink_at_temp_name_is_not_followed(tmp_path):
    target = tmp_path / "victim"
    target.write_text("keep")
    f = tmp_path / "x"
    os.symlink(str(target), "%s.%d.%d.tmp" % (f, os.getpid(), threading.get_ident()))
    fsutil.write_atomic(str(f), "new")
    assert f.read_text() == "new"
    assert target.read_text() == "keep"
    assert sorted(os.listdir(str(tmp_path))) == ["victim", "x"]


def test_stale_temp_file_is_replaced(tmp_path):
    f = tmp_path / "x"
    (tmp_path / ("x.%d.%d.tmp" % (os.getpid(), threading.get_ident()))).write_text("junk")
    fsutil.write_atomic(str(f), "new")
    assert f.read_text() == "new"
    assert os.listdir(str(tmp_path)) == ["x"]


@pytest.mark.parametrize("exc", [RuntimeError, KeyboardInterrupt])
def test_failure_keeps_old_content_and_leaves_no_temp(tmp_path, monkeypatch, exc):
    f = tmp_path / "x"
    f.write_text("old")

    def boom(*a):
        raise exc
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(exc):
        fsutil.write_atomic(str(f), "new")
    assert f.read_text() == "old"
    assert os.listdir(str(tmp_path)) == ["x"]


def test_surrogate_text_round_trips_as_original_bytes(tmp_path):
    f = tmp_path / "x"
    fsutil.write_atomic(str(f), "a\udcffb")
    assert f.read_bytes() == b"a\xffb"


def test_append_line_rejects_non_ascii_and_writes_nothing(tmp_path):
    f = tmp_path / "u.jsonl"
    f.write_text("one\n")
    with pytest.raises(ValueError):
        fsutil.append_line(str(f), "café\n")
    assert f.read_text() == "one\n"
    g = tmp_path / "new.jsonl"
    with pytest.raises(ValueError):
        fsutil.append_line(str(g), "é")
    assert not g.exists()


def test_append_line_tightens_existing_file(tmp_path, umask002):
    f = tmp_path / "u.jsonl"
    f.write_text("one\n")
    os.chmod(f, 0o664)
    fsutil.append_line(str(f), "two\n")
    assert f.read_text() == "one\ntwo\n"
    assert mode(f) == 0o600


def test_append_line_creates_private_file_and_dirs(tmp_path, umask002):
    f = tmp_path / "n" / "u.jsonl"
    fsutil.append_line(str(f), "x\n")
    assert mode(f) == 0o600 and mode(tmp_path / "n") == 0o700


def test_sync_write_atomic_is_the_fsutil_writer():
    assert sync._write_atomic is fsutil.write_atomic


def test_append_line_refuses_symlink(tmp_path):
    target = tmp_path / "victim"
    target.write_text("keep")
    os.chmod(target, 0o664)
    link = tmp_path / "u.jsonl"
    os.symlink(str(target), str(link))
    with pytest.raises(OSError):
        fsutil.append_line(str(link), "x\n")
    assert target.read_text() == "keep" and mode(target) == 0o664


def test_append_line_refuses_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "u.jsonl"
    os.mkfifo(str(fifo))
    rd = os.open(str(fifo), os.O_RDONLY | os.O_NONBLOCK)  # a reader, so open(O_WRONLY) would succeed
    try:
        with pytest.raises(OSError):
            fsutil.append_line(str(fifo), "x\n")
    finally:
        os.close(rd)


def test_writes_loop_over_short_os_write(tmp_path, monkeypatch):
    real = os.write
    monkeypatch.setattr(os, "write", lambda fd, b: real(fd, bytes(b)[:3]))
    fsutil.write_atomic(str(tmp_path / "x"), "abcdefghij")
    fsutil.append_line(str(tmp_path / "y"), "abcdefghij\n")
    assert (tmp_path / "x").read_text() == "abcdefghij"
    assert (tmp_path / "y").read_text() == "abcdefghij\n"


def test_real_launch_order_claim_marker_then_write_is_private(tmp_path, monkeypatch, umask002):
    cache = tmp_path / "p" / "cache"
    monkeypatch.setenv("PROJ_CACHE", str(cache))
    assert sync.claim_marker(".syncing-live") is True
    sync._write_cache("s-host", {"a": 1})
    assert mode(cache) == 0o700 and mode(tmp_path / "p") == 0o700
    assert mode(cache / ".syncing-live") == 0o600
    assert mode(cache / "s-host.json") == 0o600


def test_zero_progress_write_raises_and_leaves_no_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "write", lambda fd, b: 0)
    with pytest.raises(OSError):
        fsutil.write_atomic(str(tmp_path / "x"), "abc")
    assert os.listdir(str(tmp_path)) == []
    with pytest.raises(OSError):
        fsutil.append_line(str(tmp_path / "y"), "abc\n")


def test_append_line_to_fifo_without_reader_fails_at_once(tmp_path):
    fifo = tmp_path / "u.jsonl"
    os.mkfifo(str(fifo))
    out = []

    def run():
        try:
            fsutil.append_line(str(fifo), "x\n")
        except OSError as e:
            out.append(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(2)
    if t.is_alive():  # unblock the stuck open so the thread can end
        os.close(os.open(str(fifo), os.O_RDONLY | os.O_NONBLOCK))
        t.join(2)
        pytest.fail("append_line blocked on a FIFO with no reader")
    assert len(out) == 1 and out[0].errno == errno.ENXIO
