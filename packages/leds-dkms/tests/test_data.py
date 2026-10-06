import os
import unittest

from ugreen_leds.config import load_config

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class PackagedConfigTest(unittest.TestCase):
    def test_defaults_and_commented_template_load(self):
        config = load_config([os.path.join(DATA, "leds.toml"),
                              os.path.join(DATA, "ugreen-dxp-pve-leds.toml")])
        self.assertEqual(list(config.leds),
                         ["power", "netdev"] + [f"disk{n}" for n in range(1, 9)])
        self.assertEqual(config.leds["power"].default, "NORMAL")
        self.assertEqual(config.leds["netdev"].default, "NETDEV")
        self.assertEqual(config.leds["disk1"].default, "OFF")
        self.assertIsNone(config.i2c_bus)

    def test_fault_beats_normal_and_disk_severity_order(self):
        config = load_config([os.path.join(DATA, "leds.toml")])
        p = {name: state.priority for name, state in config.states.items()}
        self.assertGreater(p["FAULT"], p["NORMAL"])
        order = ["OFF", "ONLINE", "SPINDOWN", "ONLINE_ALERT", "CHECKING", "OFFLINE",
                 "RESILVER", "DEGRADED", "MISSING", "REMOVED", "UNAVAIL", "ERROR", "FAULTED"]
        self.assertEqual(sorted(order, key=p.__getitem__), order)

    def test_disk_states_clear_the_trigger_then_keep_the_zfs_write_order(self):
        config = load_config([os.path.join(DATA, "leds.toml")])
        self.assertEqual(config.states["DEGRADED"].attrs,
                         (("trigger", "none"), ("color", "80 40 0"),
                          ("blink_type", "blink 500 500"), ("brightness", "255")))
        self.assertEqual(config.states["CHECKING"].attrs,
                         (("trigger", "none"), ("blink_type", "blink 100 100")))


if __name__ == "__main__":
    unittest.main()
