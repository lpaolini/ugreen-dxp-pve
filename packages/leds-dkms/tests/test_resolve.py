import unittest

from tests.helpers import make_config
from ugreen_leds.contribution import Look
from ugreen_leds.resolve import OFF, diff, resolve

ONLINE = "priority=70 color=#002800 state=ONLINE\n"
FAULTED = "priority=10 color=#500000 effect=blink:500:500 state=FAULTED\n"


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.config = make_config()

    def test_defaults_when_nothing_published(self):
        result = resolve({}, self.config)
        self.assertEqual(list(result.looks), ["power"] + [f"disk{n}" for n in range(1, 9)])
        self.assertEqual(result.looks["power"], Look(100, "#004010", state="NORMAL"))
        self.assertEqual(result.looks["disk1"], OFF)
        self.assertEqual(result.sources["disk1"], "default")
        self.assertEqual(result.contributors["disk1"], [])
        self.assertEqual(result.problems, [])

    def test_lowest_priority_wins_whatever_the_order(self):
        for scanned in ({"disk1": [("smart", FAULTED), ("truenas-zfs", ONLINE)]},
                        {"disk1": [("a-zfs", ONLINE), ("z-smart", FAULTED)]}):
            with self.subTest(scanned=scanned):
                result = resolve(scanned, self.config)
                self.assertEqual(result.looks["disk1"].state, "FAULTED")
                self.assertEqual(len(result.contributors["disk1"]), 2)
        self.assertEqual(result.sources["disk1"], "z-smart")

    def test_tie_goes_to_first_producer_name(self):
        scanned = {"disk1": [("alpha", "priority=50 color=#111111\n"),
                             ("zeta", "priority=50 color=#222222\n")]}
        result = resolve(scanned, self.config)
        self.assertEqual(result.looks["disk1"].color, "#111111")
        self.assertEqual(result.sources["disk1"], "alpha")

    def test_invalid_entries_are_ignored_and_reported(self):
        scanned = {
            "power": [("fan", "FAULT\n"), ("x", "priority=1\n"), ("y", "priority=5 color=#ff0000\n")],
            "netdev": [("manual", ONLINE)],
            "disk9": [("zfs", ONLINE)],
        }
        result = resolve(scanned, self.config)
        self.assertEqual(result.looks["power"], Look(5, "#ff0000"))
        self.assertEqual(result.sources["power"], "y")
        self.assertEqual(result.problems, [
            "manual/netdev: the network LED is driven by the daemon",
            "zfs/disk9: unknown LED",
            "fan/power: expected key=value, got 'FAULT'",
            "x/power: missing color",
        ])


class DiffTest(unittest.TestCase):
    def test_changed_and_new_leds(self):
        previous = {"power": "NORMAL", "disk1": "OFF"}
        current = {"power": "FAULT", "disk1": "OFF", "disk2": "OFF"}
        self.assertEqual(diff(previous, current), ["power", "disk2"])


if __name__ == "__main__":
    unittest.main()
