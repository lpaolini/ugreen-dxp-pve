import tomllib
import unittest

from ugreen_leds.tomledit import set_key, toml_value

TEXT = """# top comment
# vmid = 100
debug = false

[bind]
# Force the bus.
# i2c_bus = 1

[netdev]  # the network LED
device_name = "vmbr0"
color = "#0040ff"

[power.NORMAL]
priority = 100
color = "#004010"
"""


class ToTomlTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(toml_value(True), "true")
        self.assertEqual(toml_value(3), "3")
        self.assertEqual(toml_value(0.75), "0.75")
        self.assertEqual(toml_value('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(toml_value([[30, 90], [35, 120]]), "[[30, 90], [35, 120]]")
        self.assertEqual(toml_value(["/a", "/b"]), '["/a", "/b"]')

    def test_strings_survive_a_toml_round_trip(self):
        for value in ("^it[0-9]+$", "(?i)(drivetemp|nvme)", "café", 'q"uote\\',
                      "\U0001F600", "a\x7fb", "tab\there"):
            with self.subTest(value=value):
                self.assertEqual(tomllib.loads(f"x = {toml_value(value)}")["x"], value)


class SetKeyTest(unittest.TestCase):
    def test_replaces_a_commented_key_in_its_table(self):
        text = set_key(TEXT, "bind", "i2c_bus", 3)
        self.assertIn("# Force the bus.\ni2c_bus = 3\n\n[netdev]", text)
        self.assertEqual(tomllib.loads(text)["bind"], {"i2c_bus": 3})

    def test_only_touches_the_named_table(self):
        text = set_key(TEXT, "netdev", "color", "#000001")
        data = tomllib.loads(text)
        self.assertEqual(data["netdev"]["color"], "#000001")
        self.assertEqual(data["power"]["NORMAL"]["color"], "#004010")

    def test_dotted_table(self):
        data = tomllib.loads(set_key(TEXT, "power.NORMAL", "color", "#123456"))
        self.assertEqual(data["power"]["NORMAL"]["color"], "#123456")

    def test_inserts_a_missing_key_after_the_header(self):
        text = set_key(TEXT, "power.NORMAL", "effect", "blink:1:1")
        self.assertIn('[power.NORMAL]\neffect = "blink:1:1"\npriority = 100', text)

    def test_top_level(self):
        text = set_key(TEXT, None, "vmid", 105)
        self.assertIn("# top comment\nvmid = 105\ndebug = false", text)
        self.assertTrue(set_key(TEXT, None, "debug", True).count("debug = true") == 1)

    def test_key_sharing_a_prefix_is_not_matched(self):
        text = set_key('[a]\ninterval_ms = 1\n', "a", "interval", 2)
        self.assertEqual(tomllib.loads(text)["a"], {"interval": 2, "interval_ms": 1})

    def test_real_key_wins_over_a_commented_example(self):
        text = set_key('[a]\n# color = "#fff"\ncolor = "#000"\n', "a", "color", "#123")
        self.assertEqual(tomllib.loads(text)["a"], {"color": "#123"})
        self.assertIn('# color = "#fff"\n', text)

    def test_insert_after_a_header_without_trailing_newline(self):
        text = set_key("[a]", "a", "k", 1)
        self.assertEqual(text, "[a]\nk = 1\n")
        self.assertEqual(tomllib.loads(text)["a"], {"k": 1})

    def test_missing_table(self):
        with self.assertRaises(KeyError):
            set_key(TEXT, "shutdown", "color", "#ffffff")


if __name__ == "__main__":
    unittest.main()
