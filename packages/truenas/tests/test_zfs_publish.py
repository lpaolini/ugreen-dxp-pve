import dataclasses
import os
import unittest
from unittest import mock

from tests.helpers import TEMPLATE, RuntimeDirectoryTestCase, load_script
from ugreen_leds.contribution import Look, parse_look
from ugreen_truenas.config import load

zfs = load_script("ugreen_truenas_zfs", "src/zfs/ugreen-truenas-zfs.py")


class ZfsPublishTest(RuntimeDirectoryTestCase):
    def setUp(self):
        super().setUp()
        zfs.configure(load(TEMPLATE))

    def look(self, led):
        return parse_look(self.read(led))

    def test_set_led_publishes_the_configured_look(self):
        self.assertTrue(zfs.set_led("2", "DEGRADED"))
        self.assertEqual(self.read("disk2"),
                         "priority=35 color=#502800 effect=blink:500:500 state=DEGRADED\n")
        self.assertTrue(zfs.set_led("2", "ONLINE"))
        self.assertEqual(self.look("disk2").state, "ONLINE")
        self.assertEqual(sorted(os.listdir(self.dir)), ["disk2"])

    def test_checking_keeps_the_bay_colour(self):
        zfs.set_led("1", "DEGRADED")
        zfs.set_all_leds("CHECKING")
        self.assertEqual(self.look("disk1"), Look(50, "#502800", "blink:100:100", "CHECKING"))
        self.assertEqual(self.look("disk2"), Look(50, "#282828", "blink:100:100", "CHECKING"))
        zfs.set_led("1", "CHECKING")  # still the DEGRADED colour, not CHECKING's own
        self.assertEqual(self.look("disk1").color, "#502800")

    def test_set_all_leds_follows_the_configured_bays(self):
        self.assertTrue(zfs.set_all_leds("OFF"))
        self.assertEqual(sorted(os.listdir(self.dir)), ["disk1", "disk2", "disk3", "disk4"])
        config = load(TEMPLATE)
        zfs.configure(dataclasses.replace(config, zfs=dataclasses.replace(config.zfs,
                                                                          bays=("/a", "/b"))))
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        zfs.set_all_leds("OFF")
        self.assertEqual(sorted(os.listdir(self.dir)), ["disk1", "disk2"])

    def test_query_failure_shows_error_on_every_bay(self):
        with mock.patch.object(zfs.subprocess, "run", side_effect=OSError("no qm")), \
                mock.patch.object(zfs, "log"):
            self.assertIsNone(zfs.fetch_guest_report())
        for n in range(1, 5):
            self.assertEqual(self.look(f"disk{n}").state, "ERROR")

    def test_outside_systemd_nothing_is_published(self):
        with mock.patch.dict(os.environ, {"RUNTIME_DIRECTORY": ""}):
            self.assertFalse(zfs.set_led("1", "DEGRADED"))


if __name__ == "__main__":
    unittest.main()
