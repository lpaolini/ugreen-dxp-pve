import os
import unittest

from ugreen_leds.config import load_config
from ugreen_leds.contribution import Look

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "ugreen-dxp-pve-leds.toml")


class TemplateTest(unittest.TestCase):
    def test_template_loads_with_the_defaults(self):
        config = load_config(TEMPLATE)
        self.assertIsNone(config.i2c_bus)
        self.assertEqual(config.power, {
            "NORMAL": Look(100, "#004010", state="NORMAL"),
            "FAULT": Look(10, "#ff0000", "blink:500:500", "FAULT"),
        })
        self.assertEqual(config.shutdown, Look(0, "#ffffff"))
        self.assertEqual(dict(config.netdev)["device_name"], "vmbr0")
        self.assertEqual(dict(config.netdev)["color"], "0 64 255")

    def test_fault_beats_normal(self):
        config = load_config(TEMPLATE)
        self.assertLess(config.power["FAULT"].priority, config.power["NORMAL"].priority)


if __name__ == "__main__":
    unittest.main()
