import errno
import os
import tempfile
import unittest

from tests.helpers import led_dir, make_led_dirs
from ugreen_leds.contribution import Look
from ugreen_leds.sysfs import apply, look_attrs, write_attr

FAULT = (("trigger", "none"), ("color", "255 0 0"), ("blink_type", "blink 500 500"))


class LookAttrsTest(unittest.TestCase):
    def test_colour_and_effect(self):
        self.assertEqual(look_attrs(Look(35, "#502800", "blink:500:500")), (
            ("trigger", "none"), ("color", "80 40 0"), ("blink_type", "blink 500 500"),
            ("brightness", "255")))

    def test_steady_and_breathing(self):
        self.assertEqual(dict(look_attrs(Look(70, "#002800")))["blink_type"], "none")
        self.assertEqual(dict(look_attrs(Look(65, "#002800", "breath:2000:0")))["blink_type"],
                         "breath 2000 0")

    def test_black_is_off_whatever_the_effect(self):
        self.assertEqual(look_attrs(Look(50, "#000000", "blink:100:100")), (
            ("trigger", "none"), ("color", "0 0 0"), ("blink_type", "none"), ("brightness", "1")))


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        make_led_dirs(self.root, ["power"])
        self.led = led_dir(self.root, "power")

    def read(self, attr):
        with open(os.path.join(self.led, attr)) as f:
            return f.read()

    def test_writes_attributes_to_files(self):
        self.assertEqual(apply(self.led, FAULT), [])
        self.assertEqual(self.read("trigger"), "none\n")
        self.assertEqual(self.read("color"), "255 0 0\n")
        self.assertEqual(self.read("blink_type"), "blink 500 500\n")

    def test_writes_in_order(self):
        calls = []
        apply(self.led, FAULT, write=lambda p, v: calls.append((os.path.basename(p), v)))
        self.assertEqual(calls, list(FAULT))

    def test_missing_led_directory_reports_every_attribute(self):
        errors = apply(led_dir(self.root, "disk1"), FAULT)
        self.assertEqual(len(errors), 3)
        self.assertIn("No such file or directory", errors[0])

    def test_failed_writes_are_reported_and_the_rest_continue(self):
        calls = []

        def write(path, value):
            calls.append(os.path.basename(path))
            if path.endswith("color"):
                raise OSError(errno.EINVAL, "Invalid argument")

        errors = apply(self.led, FAULT, write=write)
        self.assertEqual(calls, ["trigger", "color", "blink_type"])
        self.assertEqual(errors, [f"{self.led}/color='255 0 0': Invalid argument"])

    def test_write_attr_does_not_create_missing_attributes(self):
        with self.assertRaises(FileNotFoundError):
            write_attr(os.path.join(self.led, "device_name"), "vmbr0")


if __name__ == "__main__":
    unittest.main()
