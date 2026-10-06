import unittest

from ugreen_leds.color import normalize_color


class NormalizeColorTest(unittest.TestCase):
    def test_decimal_triplet_is_kept(self):
        self.assertEqual(normalize_color("0 64 16"), "0 64 16")

    def test_decimal_triplet_whitespace_is_normalised(self):
        self.assertEqual(normalize_color("  0   64 16 "), "0 64 16")

    def test_hex_forms(self):
        for value in ("#ff0010", "0xff0010", "0XFF0010", "ff0010"):
            with self.subTest(value=value):
                self.assertEqual(normalize_color(value), "255 0 16")

    def test_rejects_out_of_range_component(self):
        with self.assertRaises(ValueError):
            normalize_color("0 256 0")

    def test_rejects_garbage(self):
        for value in ("", "red", "#fff", "1 2", "1 2 3 4", "#gg0000"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_color(value)


if __name__ == "__main__":
    unittest.main()
