import contextlib
import io
import os
import tempfile
import unittest

from tests.helpers import TEMPLATE
from ugreen_truenas.config import load
from ugreen_truenas.migrate import main

FAN = """# Configuration for ugreen-truenas-fan.service.
VMID=105
DEBUG=1
POLL_INTERVAL=20
AUTO_DISCOVER_HWMON=1
FAN_HWMON_REGEX=^it[0-9]+$
FAN_PWM_CHANNEL=2
HDD_FAN_CURVE=30:80,45:255
CPU_FAN_CURVE=45:90,55:115,65:150,75:190,85:225,95:255
MIN_PWM=80
RESET_PWM_ON_EXIT=0
POWER_LED_HELPER=/usr/libexec/ugreen-dxp-pve-leds-dkms/power-led-ugreen
TEMP_CHIP_REGEX="(?i)(drivetemp|nvme)"
"""
ZFS = """VMID=105
DEBUG=0
POLL_INTERVAL=60
LEDS_OFF_ON_EXIT=1
LED_1_PATH=/sys/class/leds/ugreen:white:disk1
ALERT_THRESHOLD=0.9
BAY_3_PATH=/dev/disk/by-path/custom-3
"""


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = os.path.join(self.tmp.name, "truenas.toml")

    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def migrate(self, fan=None, zfs=None):
        argv = []
        if fan is not None:
            argv += ["--fan", self.write("fan.conf", fan)]
        if zfs is not None:
            argv += ["--zfs", self.write("zfs.conf", zfs)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(argv + [str(TEMPLATE), self.output])
        return rc, out.getvalue(), err.getvalue()

    def test_settings_are_carried_over(self):
        rc, out, _ = self.migrate(FAN, ZFS)
        self.assertEqual(rc, 0)
        config = load(self.output)
        self.assertEqual(config.vmid, "105")
        self.assertTrue(config.debug)
        self.assertEqual(config.fan.poll_interval, 20)
        self.assertEqual(config.fan.pwm_channel, 2)
        self.assertEqual(config.fan.hdd_curve, ((30, 80), (45, 255)))
        self.assertEqual(config.fan.min_pwm, 80)
        self.assertFalse(config.fan.reset_pwm_on_exit)
        self.assertEqual(config.fan.temp_chip_regex, "(?i)(drivetemp|nvme)")
        self.assertEqual(config.zfs.poll_interval, 60)
        self.assertEqual(config.zfs.alert_threshold, 0.9)
        self.assertEqual(config.zfs.bays[2], "/dev/disk/by-path/custom-3")
        self.assertEqual(config.zfs.bays[0], "/dev/disk/by-path/pci-0000:00:10.0-ata-1")
        self.assertEqual(config.disk, load(TEMPLATE).disk)
        for obsolete in ("POWER_LED_HELPER", "LEDS_OFF_ON_EXIT", "LED_1_PATH"):
            self.assertNotIn(obsolete, out)

    def test_comments_survive(self):
        self.migrate(FAN, ZFS)
        with open(self.output) as f:
            text = f.read()
        self.assertIn("# Proxmox VM ID of the TrueNAS Scale VM", text)
        self.assertIn("vmid = 105\n", text)

    def test_disagreeing_vmid_uses_the_zfs_one(self):
        rc, out, _ = self.migrate("VMID=101\n", "VMID=102\n")
        self.assertEqual(rc, 0)
        self.assertEqual(load(self.output).vmid, "102")
        self.assertIn("VMID differs", out)

    def test_one_file_is_enough(self):
        self.assertEqual(self.migrate(zfs="VMID=103\n")[0], 0)
        self.assertEqual(load(self.output).vmid, "103")

    def test_unusable_values_are_reported_and_skipped(self):
        rc, out, _ = self.migrate("MIN_PWM=lots\nFAN_FOO=1\n")
        self.assertEqual(rc, 0)
        self.assertEqual(load(self.output).fan.min_pwm, 90)
        self.assertIn("ignored MIN_PWM='lots'", out)
        self.assertIn("ignored unknown setting FAN_FOO", out)

    def test_invalid_value_is_ignored_and_the_rest_kept(self):
        rc, out, _ = self.migrate("MIN_PWM=300\nVMID=105\n")
        self.assertEqual(rc, 0)
        config = load(self.output)
        self.assertEqual(config.fan.min_pwm, 90)
        self.assertEqual(config.vmid, "105")
        self.assertIn("ignored MIN_PWM='300'", out)

    def test_duplicate_bay_is_ignored(self):
        rc, out, _ = self.migrate(zfs="VMID=105\nBAY_2_PATH=/dev/disk/by-path/pci-0000:00:10.0-ata-1\n")
        self.assertEqual(rc, 0)
        self.assertEqual(load(self.output).vmid, "105")
        self.assertIn("ignored BAY_2_PATH", out)

    def test_invalid_template_fails(self):
        bad = self.write("bad.toml", "[fan]\nmin_pwm = 300\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([bad, self.output])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(self.output))

    def test_existing_output_is_not_overwritten(self):
        self.write("truenas.toml", "keep")
        self.assertEqual(self.migrate(zfs="VMID=103\n")[0], 1)
        with open(self.output) as f:
            self.assertEqual(f.read(), "keep")


if __name__ == "__main__":
    unittest.main()
