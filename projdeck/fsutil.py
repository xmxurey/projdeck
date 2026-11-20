"""Private file writes: cache-style files are 0600, directories created for them 0700."""
import errno
import os
import stat
import threading


def makedirs_private(directory):
    """makedirs whose every new level is 0700 (makedirs' own mode only reaches the leaf)."""
    if not directory or os.path.isdir(directory):
        return
    makedirs_private(os.path.dirname(directory))
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        if not os.path.isdir(directory):
            raise


def _create_exclusive(tmp, mode):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        return os.open(tmp, flags, mode)
    except FileExistsError:
        os.remove(tmp)  # unlink, never follows a planted symlink
        return os.open(tmp, flags, mode)


def write_atomic(path, text, mode=0o600):
    """Write text to path via a fresh O_EXCL temp file (never follows a planted symlink) and
    os.replace; the temp file never outlives a failure. Text that is not valid unicode
    (undecoded path bytes) is written back as the original bytes."""
    makedirs_private(os.path.dirname(path))
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        fd = _create_exclusive(tmp, mode)
        try:
            os.fchmod(fd, mode)  # the umask may have narrowed it, never widened
            _write_all(fd, text.encode("utf-8", "surrogateescape"))
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError(errno.EIO, "write made no progress")
        view = view[n:]


def append_line(path, text, mode=0o600):
    """Append text (ASCII only; written in a loop until complete) and
    tighten an existing file to `mode`. Non-ASCII raises ValueError before anything is written."""
    data = text.encode("ascii")  # UnicodeEncodeError is a ValueError
    makedirs_private(os.path.dirname(path))
    # O_NOFOLLOW: a planted symlink is refused; O_NONBLOCK: a FIFO cannot hang the open
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, mode)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file: %s" % path)
        os.fchmod(fd, mode)
        _write_all(fd, data)
    finally:
        os.close(fd)
