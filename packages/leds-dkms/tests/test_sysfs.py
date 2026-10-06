import errno
import os
import tempfile
import unittest

from tests.helpers import make_config, make_led_dirs
from ugreen_leds.sysfs import apply, write_attr


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.config = make_config(self.root)
        make_led_dirs(self.root, ["power"])
        self.led = os.path.join(self.root, "power")

    def read(self, attr):
        with open(os.path.join(self.led, attr)) as f:
            return f.read()

    def test_writes_attributes_to_files(self):
        self.assertEqual(apply(self.led, self.config.states["FAULT"]), [])
        self.assertEqual(self.read("trigger"), "none\n")
        self.assertEqual(self.read("color"), "255 0 0\n")
        self.assertEqual(self.read("blink_type"), "blink 500 500\n")

    def test_writes_in_state_order(self):
        calls = []
        apply(self.led, self.config.states["FAULT"], write=lambda p, v: calls.append(
            (os.path.basename(p), v)))
        self.assertEqual(calls, [("trigger", "none"), ("color", "255 0 0"),
                                 ("blink_type", "blink 500 500")])

    def test_missing_led_directory_reports_every_attribute(self):
        errors = apply(os.path.join(self.root, "disk1"), self.config.states["FAULT"])
        self.assertEqual(len(errors), 3)
        self.assertIn("No such file or directory", errors[0])

    def test_failed_writes_are_reported_and_the_rest_continue(self):
        calls = []

        def write(path, value):
            calls.append(os.path.basename(path))
            if path.endswith("color"):
                raise OSError(errno.EINVAL, "Invalid argument")

        errors = apply(self.led, self.config.states["FAULT"], write=write)
        self.assertEqual(calls, ["trigger", "color", "blink_type"])
        self.assertEqual(errors, [f"{self.led}/color='255 0 0': Invalid argument"])

    def test_write_attr_does_not_create_missing_attributes(self):
        with self.assertRaises(FileNotFoundError):
            write_attr(os.path.join(self.led, "device_name"), "vmbr0")


if __name__ == "__main__":
    unittest.main()
