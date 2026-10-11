import dataclasses
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.helpers import TEMPLATE, RuntimeDirectoryTestCase, load_script
from ugreen_leds.contribution import Look, parse_look
from ugreen_truenas.config import load

zfs = load_script("ugreen_truenas_zfs", "src/zfs/ugreen-truenas-zfs.py")


class ZfsPublishTest(RuntimeDirectoryTestCase):
    def setUp(self):
        super().setUp()
        leds = tempfile.TemporaryDirectory()
        self.addCleanup(leds.cleanup)
        self.leds = leds.name
        for name in ("power", "netdev", "disk1", "disk2", "disk3", "disk4"):
            os.mkdir(os.path.join(self.leds, f"ugreen:white:{name}"))
        patcher = mock.patch.object(zfs, "LED_ROOT", self.leds)
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def test_bays_follow_the_disk_leds(self):
        self.assertEqual(zfs.BAYS, ["1", "2", "3", "4"])
        for n in (5, 6, 10):
            os.mkdir(os.path.join(self.leds, f"ugreen:white:disk{n}"))
        zfs.refresh_bays()
        self.assertEqual(zfs.BAYS, ["1", "2", "3", "4", "5", "6", "10"])

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

    def guest_report(self, report):
        """fetch_guest_report() with the guest answering `report`."""
        payload = {"exitcode": 0, "out-data": json.dumps(report)}
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(zfs.subprocess, "run", return_value=done) as run, \
                mock.patch.object(zfs, "log"):
            result = zfs.fetch_guest_report()
        return result, run.call_args.args[0][-1]

    def test_detected_bays(self):
        ata = ("pci-0000:00:10.0-ata-1\tsda\tWDC WD40EFRX\tactive/idle\n"
               "pci-0000:00:10.0-ata-3.0\tsdc\tST4000VN008\tstandby\n"
               "pci-0000:00:1f.2-ata-1\tsdd\tQEMU HARDDISK   \t\n"
               "pci-0000:00:1f.2-ata-3\tsr0\tQEMU DVD-ROM    \t\n")
        result, command = self.guest_report({"lsblk": {}, "zpool": {}, "ata": ata})
        self.assertIn("ata:$ata", command)
        bays, spindown, _lsblk, _zpool = result
        self.assertEqual(bays, {"1": "sda", "3": "sdc"})
        self.assertEqual(spindown, {"1": "active/idle", "3": "standby"})

    def test_disks_on_several_controllers_show_error(self):
        ata = ("pci-0000:00:10.0-ata-1\tsda\tWDC WD40EFRX\t\n"
               "pci-0000:00:11.0-ata-1\tsdb\tWDC WD40EFRX\t\n")
        result, _ = self.guest_report({"lsblk": {}, "zpool": {}, "ata": ata})
        self.assertIsNone(result)
        for n in range(1, 5):
            self.assertEqual(self.look(f"disk{n}").state, "ERROR")

    def test_configured_bays_are_queried_by_path(self):
        config = load(TEMPLATE)
        zfs.configure(dataclasses.replace(config, zfs=dataclasses.replace(config.zfs,
                                                                          bays=("/a", "/b"))))
        result, command = self.guest_report({"lsblk": {}, "zpool": {}, "bays": {"1": "sda"},
                                             "spindown": {"1": "standby"}})
        self.assertNotIn("ata:$ata", command)
        self.assertEqual(result[:2], ({"1": "sda"}, {"1": "standby"}))

    def test_guest_commands_are_valid_shell(self):
        for bays in ({}, {"1": "/dev/disk/by-path/x y"}):
            with self.subTest(bays=bays):
                check = subprocess.run(["sh", "-n", "-c", zfs.build_guest_cmd(bays)],
                                       capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stderr)

    def test_outside_systemd_nothing_is_published(self):
        with mock.patch.dict(os.environ, {"RUNTIME_DIRECTORY": ""}):
            self.assertFalse(zfs.set_led("1", "DEGRADED"))


if __name__ == "__main__":
    unittest.main()
