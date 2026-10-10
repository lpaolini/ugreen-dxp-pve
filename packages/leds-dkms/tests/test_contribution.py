import unittest

from ugreen_leds.contribution import Look, format_look, make_look, parse_look, rgb


class ParseLookTest(unittest.TestCase):
    def test_full_line(self):
        self.assertEqual(
            parse_look("priority=35 color=#502800 effect=blink:500:500 state=DEGRADED\n"),
            Look(35, "#502800", "blink:500:500", "DEGRADED"))

    def test_minimal_line_in_any_order_and_case(self):
        self.assertEqual(parse_look("color=#00FF00 priority=0"), Look(0, "#00ff00"))

    def test_invalid_lines(self):
        cases = {
            "": "missing priority",
            "priority=1": "missing color",
            "priority=1 color=#000000 colour=x": "unknown key 'colour'",
            "priority=1 priority=2 color=#000000": "repeated key 'priority'",
            "priority=1 color=#000000 FAULT": "expected key=value, got 'FAULT'",
            "priority=1 color=red": "color must be #rrggbb",
            "priority=1 color=#fff": "color must be #rrggbb",
            "priority=-1 color=#000000": "priority must be an integer 0-999",
            "priority=1000 color=#000000": "priority must be an integer 0-999",
            "priority=one color=#000000": "priority must be an integer 0-999",
            "priority=1 color=#000000 effect=blink:500": "effect must be",
            "priority=1 color=#000000 effect=blink:60001:0": "effect must be",
            "priority=1 color=#000000 effect=flash:1:1": "effect must be",
            "priority=1 color=#000000 state=A.B": "state must be",
            "priority=1\ncolor=#000000": "more than one line",
            "priority=1 color=#000000 state=" + "x" * 300: "longer than 256 characters",
        }
        for text, message in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                parse_look(text)


class MakeLookTest(unittest.TestCase):
    def test_rejects_non_integer_priority(self):
        for priority in (True, 1.0, "1", None):
            with self.subTest(priority=priority), self.assertRaises(ValueError):
                make_look(priority, "#000000")

    def test_rejects_non_string_colour_and_effect(self):
        with self.assertRaises(ValueError):
            make_look(1, None)
        with self.assertRaises(ValueError):
            make_look(1, "#000000", None)

    def test_limits_are_inclusive(self):
        self.assertEqual(make_look(999, "#000000", "breath:60000:0").priority, 999)


class FormatLookTest(unittest.TestCase):
    def test_optional_fields_are_left_out(self):
        self.assertEqual(format_look(Look(10, "#ff0000")), "priority=10 color=#ff0000")

    def test_round_trip(self):
        for look in (Look(10, "#ff0000"), Look(0, "#000000", "breath:2000:0"),
                     Look(999, "#a0b0c0", "blink:100:100", "CHECKING")):
            with self.subTest(look=look):
                self.assertEqual(parse_look(format_look(look)), look)


class RgbTest(unittest.TestCase):
    def test_driver_format(self):
        self.assertEqual(rgb("#ff0010"), "255 0 16")


if __name__ == "__main__":
    unittest.main()
