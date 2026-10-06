import os
import unittest

from tests.helpers import RuntimeDirectoryTestCase, load_script

zfs = load_script("ugreen_truenas_zfs", "src/zfs/ugreen-truenas-zfs.py")


class ZfsPublishTest(RuntimeDirectoryTestCase):
    def test_set_led_publishes_disk_state(self):
        self.assertTrue(zfs.set_led("2", "DEGRADED"))
        self.assertEqual(self.read("disk2"), "DEGRADED\n")
        self.assertTrue(zfs.set_led("2", "ONLINE"))
        self.assertEqual(self.read("disk2"), "ONLINE\n")
        self.assertEqual(sorted(os.listdir(self.dir)), ["disk2"])

    def test_set_all_leds(self):
        self.assertTrue(zfs.set_all_leds("CHECKING"))
        self.assertEqual(sorted(os.listdir(self.dir)), ["disk1", "disk2", "disk3", "disk4"])
        self.assertEqual(self.read("disk3"), "CHECKING\n")


if __name__ == "__main__":
    unittest.main()
