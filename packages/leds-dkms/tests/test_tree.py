import os
import tempfile
import unittest

from ugreen_leds.tree import clear, producer_dir, producer_dirs, publish, runtime_dir, scan


class TreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def write(self, relpath, content):
        path = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    def test_missing_root_is_empty(self):
        missing = os.path.join(self.root, "missing")
        self.assertEqual(producer_dirs(missing), [])
        self.assertEqual(scan(missing), {})

    def test_scan_groups_by_led_sorted_by_producer(self):
        self.write("zfs/disk2", "DEGRADED\n")
        self.write("fan/power", "FAULT\n")
        self.write("smart/disk2", "FAULTED\n")
        self.assertEqual(scan(self.root), {
            "disk2": [("smart", "FAULTED\n"), ("zfs", "DEGRADED\n")],
            "power": [("fan", "FAULT\n")],
        })

    def test_scan_skips_unreadable_entries(self):
        self.write("zfs/disk1", "ONLINE\n")
        os.symlink("/nonexistent", os.path.join(self.root, "zfs", "disk2"))
        os.mkfifo(os.path.join(self.root, "zfs", "disk3"))
        self.assertEqual(scan(self.root), {"disk1": [("zfs", "ONLINE\n")]})

    def test_scan_skips_dotfiles_dot_dirs_and_subdirs(self):
        self.write("zfs/.disk1.tmp", "ONLINE\n")
        self.write(".hidden/power", "FAULT\n")
        os.makedirs(os.path.join(self.root, "zfs", "nested"))
        self.write("stray-file", "x")
        self.assertEqual(scan(self.root), {})
        self.assertEqual(producer_dirs(self.root), [os.path.join(self.root, "zfs")])

    def test_runtime_dir(self):
        self.assertEqual(runtime_dir({"RUNTIME_DIRECTORY": "/run/a:/run/b"}), "/run/a")
        self.assertIsNone(runtime_dir({"RUNTIME_DIRECTORY": ""}))
        self.assertIsNone(runtime_dir({}))

    def test_producer_dir(self):
        cases = {
            "/run/ugreen-dxp-pve/zfs": "/run/ugreen-dxp-pve/zfs",
            "/run/ugreen-dxp-pve/fan:/run/other": "/run/ugreen-dxp-pve/fan",
            "": "/run/ugreen-dxp-pve/manual",
        }
        for runtime, expected in cases.items():
            with self.subTest(runtime=runtime):
                self.assertEqual(producer_dir(environ={"RUNTIME_DIRECTORY": runtime}), expected)
        self.assertEqual(producer_dir("/tmp/r", environ={}), "/tmp/r/manual")

    def test_publish_creates_directory_and_clear(self):
        directory = os.path.join(self.root, "manual")
        publish(directory, "power", "FAULT")
        publish(directory, "power", "NORMAL")
        self.assertEqual(os.listdir(directory), ["power"])
        with open(os.path.join(directory, "power")) as f:
            self.assertEqual(f.read(), "NORMAL\n")
        clear(directory, "power")
        clear(directory, "power")  # clearing twice is fine
        self.assertEqual(os.listdir(directory), [])


if __name__ == "__main__":
    unittest.main()
