import contextlib
import io
import os
import tempfile
import unittest

from ugreen_leds.config import load_config
from ugreen_leds.contribution import Look
from ugreen_leds.migrate import main

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "ugreen-dxp-pve-leds.toml")

# The dev-channel overrides file as shipped: every line commented out.
DEV_TEMPLATE = """# Local overrides for ugreen-dxp-pve-leds.service.
#
# [bind]
# i2c_bus = 1
#
# [states.NETDEV]
# device_name = "vmbr1"
"""

# An edited 0.9.10 /etc/ugreen-dxp-pve-leds.conf.
LEGACY = """# Configuration for ugreen-dxp-pve-leds-dkms services.
I2C_BUS=3
POWER_LED_NORMAL_COLOR="0 64 16"
POWER_LED_FAULT_BLINK_TYPE="blink 500 500"
NETDEV_LED_DEVICE=vmbr1
NETDEV_LED_COLOR="0 64 255"
"""


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = os.path.join(self.tmp.name, "leds.toml")

    def write(self, path, text):
        with open(path, "w") as f:
            f.write(text)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def migrate(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([self.config, TEMPLATE, *extra])
        return rc, out.getvalue(), err.getvalue()

    def test_fresh_install_gets_the_template(self):
        self.assertEqual(self.migrate()[0], 0)
        self.assertEqual(self.read(self.config), self.read(TEMPLATE))

    def test_missing_legacy_file_is_ignored(self):
        self.assertEqual(self.migrate("--legacy", os.path.join(self.tmp.name, "nope"))[0], 0)
        self.assertEqual(self.read(self.config), self.read(TEMPLATE))

    def test_legacy_settings_are_carried_over(self):
        legacy = os.path.join(self.tmp.name, "leds.conf.dpkg-bak")
        self.write(legacy, LEGACY)
        rc, out, _ = self.migrate("--legacy", legacy)
        self.assertEqual(rc, 0)
        config = load_config(self.config)
        self.assertEqual(config.i2c_bus, 3)
        self.assertEqual(dict(config.netdev)["device_name"], "vmbr1")
        self.assertIn("colours and blink settings", out)
        self.assertTrue(os.path.exists(legacy))  # left where dpkg put it

    def test_untouched_dev_overrides_file_becomes_the_template(self):
        self.write(self.config, DEV_TEMPLATE)
        rc, out, _ = self.migrate()
        self.assertEqual(rc, 0)
        self.assertEqual(self.read(self.config), self.read(TEMPLATE))
        self.assertEqual(self.read(self.config + ".migrated"), DEV_TEMPLATE)
        self.assertIn("previous file", out)

    def test_dev_overrides_are_converted(self):
        self.write(self.config, """
[bind]
i2c_bus = 1

[leds.power]
path = "/sys/class/leds/x"

[states.NETDEV]
device_name = "vmbr1"
brightness = 128

[states.NORMAL]
color = "0 32 8"

[states.FAULT]
priority = 200
blink_type = "blink 250 250"

[states.SHUTDOWN]
color = "#101010"

[states.DEGRADED]
color = "#ff8000"
""")
        rc, out, _ = self.migrate()
        self.assertEqual(rc, 0)
        config = load_config(self.config)
        self.assertEqual(config.i2c_bus, 1)
        self.assertEqual(dict(config.netdev)["device_name"], "vmbr1")
        self.assertEqual(config.power["NORMAL"], Look(100, "#002008", state="NORMAL"))
        self.assertEqual(config.power["FAULT"], Look(10, "#ff0000", "blink:250:250", "FAULT"))
        self.assertEqual(config.shutdown, Look(0, "#101010"))
        for expected in ("dropped [leds.power]", "dropped states.NETDEV.brightness",
                         "dropped states.FAULT.priority", "[disk.DEGRADED]"):
            self.assertIn(expected, out)

    def test_bad_values_are_dropped(self):
        self.write(self.config, '[states.NORMAL]\ncolor = "green"\n')
        rc, out, _ = self.migrate()
        self.assertEqual(rc, 0)
        self.assertEqual(load_config(self.config).power["NORMAL"].color, "#004010")
        self.assertIn("dropped states.NORMAL.color", out)

    def test_current_file_is_left_alone(self):
        legacy = os.path.join(self.tmp.name, "leds.conf.dpkg-bak")
        self.write(legacy, "I2C_BUS=7\n")
        self.write(self.config, self.read(TEMPLATE).replace('"vmbr0"', '"vmbr2"'))
        before = self.read(self.config)
        self.assertEqual(self.migrate("--legacy", legacy)[0], 0)
        self.assertEqual(self.read(self.config), before)
        self.assertFalse(os.path.exists(self.config + ".migrated"))

    def test_dev_postinst_shape_is_converted(self):
        self.write(self.config, DEV_TEMPLATE + "\n[bind]\ni2c_bus = 4\n")
        rc, _, _ = self.migrate()
        self.assertEqual(rc, 0)
        self.assertEqual(load_config(self.config).i2c_bus, 4)

    def test_bad_legacy_value_is_ignored(self):
        legacy = os.path.join(self.tmp.name, "leds.conf.dpkg-bak")
        self.write(legacy, "I2C_BUS=3\nNETDEV_LED_DEVICE=has space\n")
        rc, out, _ = self.migrate("--legacy", legacy)
        self.assertEqual(rc, 0)
        config = load_config(self.config)
        self.assertEqual(config.i2c_bus, 3)
        self.assertNotEqual(dict(config.netdev)["device_name"], "has space")
        self.assertIn("ignored NETDEV_LED_DEVICE", out)

    def test_unreadable_legacy_file_is_not_reported_as_read(self):
        legacy = os.path.join(self.tmp.name, "leds-is-a-directory")
        os.mkdir(legacy)
        rc, out, _ = self.migrate("--legacy", legacy)
        self.assertEqual(rc, 0)
        self.assertIn("ignored", out)
        self.assertNotIn("Created", out)

    def test_non_utf8_legacy_file_is_still_read(self):
        legacy = os.path.join(self.tmp.name, "leds.conf.dpkg-bak")
        with open(legacy, "wb") as f:
            f.write(b"# caf\xe9\nI2C_BUS=3\n")
        rc, _, _ = self.migrate("--legacy", legacy)
        self.assertEqual(rc, 0)
        self.assertEqual(load_config(self.config).i2c_bus, 3)

    def test_one_bad_override_drops_only_that_key(self):
        self.write(self.config, '[bind]\ni2c_bus = 2\n\n[states.NETDEV]\nlink = "1"\n')
        rc, out, _ = self.migrate()
        self.assertEqual(rc, 0)
        self.assertEqual(load_config(self.config).i2c_bus, 2)
        self.assertIn("dropped states.NETDEV.link", out)

    def test_unreadable_file_is_left_alone(self):
        self.write(self.config, "[bind\n")
        rc, _, err = self.migrate()
        self.assertEqual(rc, 0)
        self.assertEqual(self.read(self.config), "[bind\n")
        self.assertIn("leaving", err)

    def test_bad_device_name_is_dropped(self):
        self.write(self.config, '[states.NETDEV]\ndevice_name = "has space"\n')
        rc, out, _ = self.migrate()
        self.assertEqual(rc, 0)
        self.assertIn("dropped states.NETDEV.device_name", out)
        self.assertNotEqual(dict(load_config(self.config).netdev)["device_name"], "has space")

    def test_invalid_template_fails(self):
        bad = os.path.join(self.tmp.name, "bad.toml")
        self.write(bad, "[bind]\ni2c_bus = \"x\"\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([self.config, bad])
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(self.config))


if __name__ == "__main__":
    unittest.main()
