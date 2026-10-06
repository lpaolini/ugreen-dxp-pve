# SPDX-License-Identifier: MIT
"""inotify event source for the producer tree (Linux only)."""
import ctypes
import ctypes.util
import os
import select

IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_ONLYDIR = 0x01000000
IN_NONBLOCK = 0o0004000
IN_CLOEXEC = 0o2000000

ROOT_MASK = IN_CREATE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO | IN_ONLYDIR
PRODUCER_MASK = (IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_DELETE
                 | IN_DELETE_SELF | IN_ONLYDIR)


def _read_all(fd):
    """Yield everything currently readable from the non-blocking `fd`."""
    while True:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        if not data:
            return
        yield data


class InotifyWatcher:
    """Wakes the daemon when anything under the producer tree changes.

    `sync()` keeps one watch on the root and one per producer directory.
    `wait()` blocks until an inotify event or a byte on `wakeup_fd` (used for
    signals) arrives, then drains both.
    """

    def __init__(self, root, wakeup_fd=None):
        self.root = root
        self.wakeup_fd = wakeup_fd
        self._libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        self.fd = self._libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise self._error()
        self._watches = {}  # path -> watch descriptor

    def _error(self):
        err = ctypes.get_errno()
        return OSError(err, os.strerror(err))

    def _add(self, path, mask):
        wd = self._libc.inotify_add_watch(self.fd, os.fsencode(path), ctypes.c_uint32(mask))
        if wd < 0:
            raise self._error()
        return wd

    def watched(self):
        return set(self._watches)

    def sync(self, producer_dirs):
        """Watch the root plus exactly `producer_dirs`.

        Every path is re-added on each call: inotify returns the existing
        descriptor for a directory it already watches and a new one for a
        recreated directory, so no watch can go stale, even after lost events.
        """
        wanted = set(producer_dirs)
        for path in list(self._watches):
            if path != self.root and path not in wanted:
                # Fails harmlessly if the kernel already dropped it (directory deleted).
                self._libc.inotify_rm_watch(self.fd, self._watches.pop(path))
        self._watches[self.root] = self._add(self.root, ROOT_MASK)
        for path in wanted:
            try:
                self._watches[path] = self._add(path, PRODUCER_MASK)
            except FileNotFoundError:
                self._watches.pop(path, None)  # removed since the scan; the root watch reports it

    def wait(self, timeout=None):
        """Block until something happens. Returns False only on timeout."""
        fds = [self.fd] if self.wakeup_fd is None else [self.fd, self.wakeup_fd]
        readable, _, _ = select.select(fds, [], [], timeout)
        for fd in readable:  # the next pass rescans everything, so contents don't matter
            for _ in _read_all(fd):
                pass
        return bool(readable)

    def close(self):
        os.close(self.fd)
