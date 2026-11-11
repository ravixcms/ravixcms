"""A non-blocking exclusive file lock that works on Linux, macOS and Windows.

Linux and macOS use ``fcntl.flock`` exactly as before; Windows uses ``msvcrt.locking`` on the first byte of the file. Both are released by the
system when the file is closed or the process dies, so a crashed process never leaves a stuck lock. The platform modules are imported inside
``try`` blocks: importing this module can never fail, so a platform without ``fcntl`` (Windows) still starts.

``try_lock`` raises ``BlockingIOError`` when somebody else holds the lock and another ``OSError`` when the file system cannot lock at all.
"""
from __future__ import annotations

import errno
import os

try:  # Linux, macOS
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:  # Windows
    import msvcrt
except ImportError:  # Linux, macOS
    msvcrt = None

_BUSY = (errno.EACCES, errno.EAGAIN, errno.EDEADLK, getattr(errno, "EWOULDBLOCK", errno.EAGAIN))


def supported() -> bool:
    return fcntl is not None or msvcrt is not None


def try_lock(fd: int) -> None:
    """Takes the lock on an open file descriptor or raises (see module doc). Never blocks."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    if msvcrt is None:
        raise OSError(errno.ENOSYS, "file locking is not available on this platform")
    os.lseek(fd, 0, os.SEEK_SET)  # msvcrt locks from the current position: always the same byte, so two processes meet
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if exc.errno in _BUSY:
            raise BlockingIOError(exc.errno, "the lock is held by another process") from None
        raise


def unlock(fd: int) -> None:
    """Optional: closing the descriptor releases the lock too."""
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
