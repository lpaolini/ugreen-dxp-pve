import os
import shutil
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform.startswith("linux"), "inotify is Linux-only")
class InotifyWatcherTest(unittest.TestCase):
    def setUp(self):
        from ugreen_leds.watch import InotifyWatcher

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_r, False)
        self.addCleanup(os.close, self.wake_r)
        self.addCleanup(os.close, self.wake_w)
        self.watcher = InotifyWatcher(self.root, wakeup_fd=self.wake_r)
        self.addCleanup(self.watcher.close)
        self.watcher.sync([])

    def producer(self, name):
        path = os.path.join(self.root, name)
        os.makedirs(path)
        return path

    def test_quiet_tree_times_out(self):
        self.assertFalse(self.watcher.wait(timeout=0.05))

    def test_new_producer_directory_wakes(self):
        self.producer("zfs")
        self.assertTrue(self.watcher.wait(timeout=1))
        self.assertFalse(self.watcher.wait(timeout=0.05))  # events were drained

    def test_file_written_in_producer_directory_wakes(self):
        zfs = self.producer("zfs")
        self.watcher.wait(timeout=1)
        self.watcher.sync([zfs])
        with open(os.path.join(zfs, "disk1"), "w") as f:
            f.write("ONLINE\n")
        self.assertTrue(self.watcher.wait(timeout=1))

    def test_recreated_directory_is_watched_again(self):
        zfs = self.producer("zfs")
        self.watcher.wait(timeout=1)
        self.watcher.sync([zfs])
        shutil.rmtree(zfs)
        self.assertTrue(self.watcher.wait(timeout=1))
        os.makedirs(zfs)  # e.g. the producer restarted
        self.watcher.wait(timeout=1)
        self.watcher.sync([zfs])  # same path, new inode: must get a fresh watch
        self.assertFalse(self.watcher.wait(timeout=0.05))
        with open(os.path.join(zfs, "disk1"), "w") as f:
            f.write("ONLINE\n")
        self.assertTrue(self.watcher.wait(timeout=1))

    def test_sync_drops_directories_no_longer_listed(self):
        zfs = self.producer("zfs")
        self.watcher.sync([zfs])
        self.watcher.sync([])
        self.assertEqual(self.watcher.watched(), {self.root})

    def test_wakeup_fd_wakes_and_is_drained(self):
        os.write(self.wake_w, b"\0")
        self.assertTrue(self.watcher.wait(timeout=1))
        self.assertFalse(self.watcher.wait(timeout=0.05))


if __name__ == "__main__":
    unittest.main()
