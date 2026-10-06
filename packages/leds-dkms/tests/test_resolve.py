import unittest

from tests.helpers import make_config
from ugreen_leds.resolve import diff, parse_state, resolve


class ParseStateTest(unittest.TestCase):
    def test_valid(self):
        for raw in ("FAULT\n", "FAULT", "  FAULT \n"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_state(raw), "FAULT")

    def test_malformed(self):
        for raw in ("", "\n", "FAULT\nNORMAL\n", "TWO WORDS"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_state(raw))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.config = make_config()

    def test_defaults_when_nothing_published(self):
        result = resolve({}, self.config)
        self.assertEqual(result.states, {"power": "NORMAL", "disk1": "OFF"})
        self.assertEqual(result.contributors, {"power": [], "disk1": []})
        self.assertEqual(result.problems, [])

    def test_highest_priority_wins(self):
        scanned = {"disk1": [("smart", "FAULTED\n"), ("zfs", "ONLINE\n")]}
        result = resolve(scanned, self.config)
        self.assertEqual(result.states["disk1"], "FAULTED")
        self.assertEqual(result.contributors["disk1"], [("smart", "FAULTED"), ("zfs", "ONLINE")])

    def test_tie_goes_to_first_producer_name(self):
        # Equal priority, different states: alphabetical producer wins.
        scanned = {"disk1": [("zfs", "OFF\n"), ("alpha", "NORMAL\n")]}
        self.assertEqual(resolve(scanned, self.config).states["disk1"], "NORMAL")

    def test_invalid_entries_are_ignored_and_reported(self):
        scanned = {
            "power": [("fan", "BOGUS\n"), ("x", "\n")],
            "disk9": [("zfs", "ONLINE\n")],
        }
        result = resolve(scanned, self.config)
        self.assertEqual(result.states["power"], "NORMAL")
        self.assertEqual(result.contributors["power"], [])
        self.assertEqual(result.problems, [
            "zfs/disk9: unknown LED",
            "fan/power: unknown state 'BOGUS'",
            "x/power: malformed content '\\n'",
        ])


class DiffTest(unittest.TestCase):
    def test_changed_and_new_leds(self):
        previous = {"power": "NORMAL", "disk1": "OFF"}
        current = {"power": "FAULT", "disk1": "OFF", "disk2": "OFF"}
        self.assertEqual(diff(previous, current), ["power", "disk2"])


if __name__ == "__main__":
    unittest.main()
