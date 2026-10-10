import os
import tempfile
import unittest

from ugreen_leds.config import ConfigError, build, load_config
from ugreen_leds.contribution import Look

MINIMAL = {
    "netdev": {"device_name": "eth0", "color": "#0040ff"},
    "shutdown": {"color": "#ffffff"},
    "power": {"NORMAL": {"priority": 100, "color": "#004010"}},
}
NORMAL = {"priority": 100, "color": "#004010"}


def changed(section, value):
    """MINIMAL with one top-level section replaced (None removes it)."""
    data = dict(MINIMAL)
    if value is None:
        data.pop(section, None)
    else:
        data[section] = value
    return data


class BuildTest(unittest.TestCase):
    def test_minimal_config(self):
        config = build(MINIMAL)
        self.assertIsNone(config.i2c_bus)
        self.assertEqual(config.power, {"NORMAL": Look(100, "#004010", state="NORMAL")})
        self.assertEqual(config.shutdown, Look(0, "#ffffff"))
        self.assertEqual(config.netdev, (
            ("trigger", "netdev"), ("device_name", "eth0"), ("brightness", "255"),
            ("color", "0 64 255"), ("link", "1"), ("tx", "1"), ("rx", "1"), ("interval", "50"),
        ))
        self.assertEqual(config.led_path("disk1"), "/sys/class/leds/ugreen:white:disk1")

    def test_led_root(self):
        self.assertEqual(build(MINIMAL, led_root="/tmp/x").led_path("power"),
                         "/tmp/x/ugreen:white:power")

    def test_power_looks(self):
        config = build(changed("power", {
            "NORMAL": NORMAL,
            "FAULT": {"priority": 10, "color": "#FF0000", "effect": "blink:500:500"},
        }))
        self.assertEqual(config.power["FAULT"], Look(10, "#ff0000", "blink:500:500", "FAULT"))

    def test_netdev_settings(self):
        config = build(changed("netdev", {"device_name": "vmbr1", "color": "#000001",
                                          "link": True, "tx": 0, "rx": False, "interval": 100}))
        self.assertEqual(dict(config.netdev), {
            "trigger": "netdev", "device_name": "vmbr1", "brightness": "255", "color": "0 0 1",
            "link": "1", "tx": "0", "rx": "0", "interval": "100"})

    def test_i2c_bus(self):
        self.assertEqual(build({**MINIMAL, "bind": {"i2c_bus": 1}}).i2c_bus, 1)
        for bad in (-1, "1", True):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                build({**MINIMAL, "bind": {"i2c_bus": bad}})

    def test_invalid_configs(self):
        eth0 = {"device_name": "eth0", "color": "#000000"}
        cases = {
            "old states table": {**MINIMAL, "states": {}},
            "old leds table": {**MINIMAL, "leds": {}},
            "no power looks": changed("power", None),
            "no NORMAL": changed("power", {"FAULT": NORMAL}),
            "bad look name": changed("power", {"NORMAL": NORMAL, "BAD NAME": NORMAL}),
            "look not a table": changed("power", {"NORMAL": 1}),
            "power not a table": changed("power", 1),
            "unknown look key": changed("power", {"NORMAL": {**NORMAL, "brightness": 1}}),
            "rgb colour": changed("power", {"NORMAL": {**NORMAL, "color": "0 64 16"}}),
            "old blink format": changed("power", {"NORMAL": {**NORMAL, "effect": "blink 500 500"}}),
            "missing priority": changed("power", {"NORMAL": {"color": "#004010"}}),
            "no shutdown colour": changed("shutdown", {}),
            "unknown shutdown key": changed("shutdown", {"color": "#ffffff", "x": 1}),
            "no netdev": changed("netdev", None),
            "device name with space": changed("netdev", {**eth0, "device_name": "vm br0"}),
            "device name too long": changed("netdev", {**eth0, "device_name": "x" * 16}),
            "netdev flag 2": changed("netdev", {**eth0, "link": 2}),
            "netdev interval 0": changed("netdev", {**eth0, "interval": 0}),
            "unknown netdev key": changed("netdev", {**eth0, "trigger": "none"}),
            "bind not a table": {**MINIMAL, "bind": 1},
            "unknown bind key": {**MINIMAL, "bind": {"i2c-bus": 1}},
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(ConfigError):
                build(data)

    def test_errors_name_the_setting(self):
        with self.assertRaisesRegex(ConfigError, r"power\.NORMAL: color must be #rrggbb"):
            build(changed("power", {"NORMAL": {"priority": 100, "color": "green"}}))


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_reads_a_file(self):
        path = self.write("leds.toml", """
[netdev]
device_name = "vmbr0"
color = "#0040ff"

[shutdown]
color = "#ffffff"

[power.NORMAL]
priority = 100
color = "#004010"
""")
        config = load_config(path, led_root="/x")
        self.assertEqual(dict(config.netdev)["device_name"], "vmbr0")
        self.assertEqual(config.led_root, "/x")

    def test_errors_name_the_file(self):
        missing = os.path.join(self.tmp.name, "missing.toml")
        not_utf8 = os.path.join(self.tmp.name, "latin1.toml")
        with open(not_utf8, "wb") as f:
            f.write(b"# caf\xe9\n")
        for path in (self.write("broken.toml", "[netdev\n"), missing, not_utf8, self.tmp.name):
            with self.subTest(path=path):
                with self.assertRaises(ConfigError) as ctx:
                    load_config(path)
                self.assertIn(path, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
