# SPDX-License-Identifier: MIT
"""inotify event source for the producer tree (Linux only)."""
import ctypes
import ctypes.util
import os
import select
import struct

IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_NONBLOCK = 0o0004000
IN_CLOEXEC = 0o2000000

ROOT_MASK = IN_CREATE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO | IN_ONLYDIR
PRODUCER_MASK = (IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_DELETE
                 | IN_DELETE_SELF | IN_ONLYDIR)

_EVENT = struct.Struct("iIII")


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
        """Watch the root plus exactly `producer_dirs`."""
        wanted = set(producer_dirs)
        for path in list(self._watches):
            if path != self.root and path not in wanted:
                # Fails harmlessly if the kernel already dropped it (directory deleted).
                self._libc.inotify_rm_watch(self.fd, self._watches.pop(path))
        if self.root not in self._watches:
            self._watches[self.root] = self._add(self.root, ROOT_MASK)
        for path in wanted - set(self._watches):
            try:
                self._watches[path] = self._add(path, PRODUCER_MASK)
            except FileNotFoundError:
                pass  # removed since the scan; the root watch reports it

    def wait(self, timeout=None):
        """Block until something happens. Returns False only on timeout."""
        fds = [self.fd] if self.wakeup_fd is None else [self.fd, self.wakeup_fd]
        readable, _, _ = select.select(fds, [], [], timeout)
        if self.wakeup_fd in readable:
            for _ in _read_all(self.wakeup_fd):
                pass
        if self.fd in readable:
            self._read_events()
        return bool(readable)

    def _read_events(self):
        for data in _read_all(self.fd):
            offset = 0
            while offset + _EVENT.size <= len(data):
                wd, mask, _cookie, name_len = _EVENT.unpack_from(data, offset)
                offset += _EVENT.size + name_len
                if mask & IN_IGNORED:  # the kernel dropped this watch; sync() re-adds it
                    self._watches = {p: w for p, w in self._watches.items() if w != wd}

    def close(self):
        os.close(self.fd)
