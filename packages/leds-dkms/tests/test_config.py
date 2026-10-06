import os
import tempfile
import unittest

from ugreen_leds.config import ConfigError, build, deep_merge, load_config

MINIMAL = {
    "leds": {"power": {"path": "/sys/class/leds/power", "default": "NORMAL"}},
    "states": {"NORMAL": {"priority": 0, "color": "0 64 16"}},
}


def with_state(**table):
    data = {"leds": MINIMAL["leds"], "states": {"NORMAL": {"priority": 0, **table}}}
    return build(data).states["NORMAL"]


class DeepMergeTest(unittest.TestCase):
    def test_override_replaces_leaf_and_keeps_order(self):
        base = {"states": {"A": {"priority": 0, "color": "1 1 1", "brightness": 5}}}
        override = {"states": {"A": {"color": "2 2 2"}, "B": {"priority": 1}}}
        merged = deep_merge(base, override)
        self.assertEqual(list(merged["states"]["A"].items()),
                         [("priority", 0), ("color", "2 2 2"), ("brightness", 5)])
        self.assertEqual(merged["states"]["B"], {"priority": 1})

    def test_inputs_are_not_mutated(self):
        base = {"a": {"b": 1}}
        deep_merge(base, {"a": {"b": 2}})
        self.assertEqual(base, {"a": {"b": 1}})


class BuildTest(unittest.TestCase):
    def test_minimal_config(self):
        config = build(MINIMAL)
        self.assertEqual(config.leds["power"].default, "NORMAL")
        self.assertEqual(config.states["NORMAL"].attrs, (("color", "0 64 16"),))
        self.assertIsNone(config.i2c_bus)

    def test_trigger_is_written_first_then_table_order(self):
        state = with_state(brightness=255, trigger="netdev", device_name="vmbr0")
        self.assertEqual([key for key, _ in state.attrs],
                         ["trigger", "brightness", "device_name"])

    def test_values_become_strings_and_colors_are_normalised(self):
        state = with_state(color="#ff0000", link=1, tx=True)
        self.assertEqual(dict(state.attrs), {"color": "255 0 0", "link": "1", "tx": "1"})

    def test_i2c_bus(self):
        self.assertEqual(build({**MINIMAL, "bind": {"i2c_bus": 1}}).i2c_bus, 1)
        for bad in (-1, "1", True):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                build({**MINIMAL, "bind": {"i2c_bus": bad}})

    def test_invalid_configs(self):
        cases = {
            "unknown top-level": {**MINIMAL, "extra": {}},
            "no leds": {"states": MINIMAL["states"]},
            "relative path": {"leds": {"power": {"path": "sys/x", "default": "NORMAL"}},
                              "states": MINIMAL["states"]},
            "unknown default": {"leds": {"power": {"path": "/x", "default": "NOPE"}},
                                "states": MINIMAL["states"]},
            "unknown led key": {"leds": {"power": {"path": "/x", "default": "NORMAL", "x": 1}},
                                "states": MINIMAL["states"]},
            "missing priority": {"leds": MINIMAL["leds"], "states": {"NORMAL": {}}},
            "bool priority": {"leds": MINIMAL["leds"], "states": {"NORMAL": {"priority": True}}},
            "bad state name": {"leds": MINIMAL["leds"],
                               "states": {**MINIMAL["states"], "BAD NAME": {"priority": 0}}},
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(ConfigError):
                build(data)

    def test_invalid_state_attributes(self):
        cases = {
            "color": {"color": "red"},
            "blink_type": {"blink_type": "blink 500"},
            "brightness range": {"brightness": 256},
            "brightness type": {"brightness": "255"},
            "path traversal": {"../color": "1"},
            "float": {"interval": 1.5},
        }
        for label, table in cases.items():
            with self.subTest(label), self.assertRaises(ConfigError):
                with_state(**table)

    def test_valid_blink_types(self):
        for value in ("none", "blink 500 500", "breath 2000 0"):
            with self.subTest(value=value):
                self.assertEqual(dict(with_state(blink_type=value).attrs)["blink_type"], value)


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_layers_merge_and_missing_files_are_skipped(self):
        base = self.write("base.toml", """
[leds.power]
path = "/sys/class/leds/power"
default = "NORMAL"

[states.NORMAL]
priority = 0
color = "0 64 16"
brightness = 255
""")
        override = self.write("override.toml", """
[states.NORMAL]
color = "#ff0000"
""")
        missing = os.path.join(self.tmp.name, "missing.toml")
        config = load_config([base, missing, override])
        self.assertEqual(config.states["NORMAL"].attrs,
                         (("color", "255 0 0"), ("brightness", "255")))

    def test_syntax_error_names_the_file(self):
        path = self.write("broken.toml", "[leds\n")
        with self.assertRaises(ConfigError) as ctx:
            load_config([path])
        self.assertIn("broken.toml", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
