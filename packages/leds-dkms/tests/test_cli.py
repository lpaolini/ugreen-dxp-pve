import contextlib
import io
import json
import os
import tempfile
import unittest

from ugreen_leds.cli import format_status, main

CONFIG = """
[leds.power]
path = "/sys/class/leds/power"
default = "NORMAL"

[states.NORMAL]
priority = 0

[states.FAULT]
priority = 100
"""


class FormatStatusTest(unittest.TestCase):
    def test_format(self):
        data = {
            "leds": {
                "power": {"state": "FAULT", "applied": True, "errors": [],
                          "contributors": [{"producer": "fan", "state": "FAULT"}]},
                "disk5": {"state": "OFF", "applied": False, "contributors": [],
                          "errors": ["LED path missing: /sys/class/leds/x"]},
            },
            "problems": ["zfs/disk9: unknown LED"],
        }
        self.assertEqual(format_status(data).splitlines(), [
            "power    FAULT          fan=FAULT",
            "disk5    OFF            (default)  [not applied]",
            "         error: LED path missing: /sys/class/leds/x",
            "problem: zfs/disk9: unknown LED",
        ])


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_root = os.path.join(self.tmp.name, "run")
        self.config = os.path.join(self.tmp.name, "leds.toml")
        with open(self.config, "w") as f:
            f.write(CONFIG)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--config", self.config, "--run-root", self.run_root,
                "--status-file", os.path.join(self.tmp.name, "status"), *args]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def manual_file(self):
        return os.path.join(self.run_root, "manual", "power")

    def test_set_and_clear(self):
        self.assertEqual(self.run_cli("set", "power", "FAULT")[0], 0)
        with open(self.manual_file()) as f:
            self.assertEqual(f.read(), "FAULT\n")
        self.assertEqual(self.run_cli("clear", "power")[0], 0)
        self.assertFalse(os.path.exists(self.manual_file()))

    def test_unknown_led_and_state_are_rejected(self):
        rc, _, err = self.run_cli("set", "disk9", "FAULT")
        self.assertEqual(rc, 2)
        self.assertIn("unknown LED 'disk9'", err)
        rc, _, err = self.run_cli("set", "power", "BOGUS")
        self.assertEqual(rc, 2)
        self.assertIn("unknown state 'BOGUS'", err)
        self.assertFalse(os.path.exists(self.manual_file()))

    def test_write_failure_is_an_error_not_a_traceback(self):
        open(self.run_root, "w").close()  # a file where the run directory should be
        rc, _, err = self.run_cli("set", "power", "FAULT")
        self.assertEqual(rc, 1)
        self.assertIn("ERROR:", err)

    def test_status(self):
        rc, _, err = self.run_cli("status")
        self.assertEqual(rc, 1)
        self.assertIn("daemon status unavailable", err)
        with open(os.path.join(self.tmp.name, "status"), "w") as f:
            json.dump({"leds": {"power": {"state": "NORMAL", "applied": True,
                                          "contributors": [], "errors": []}},
                       "problems": []}, f)
        rc, out, _ = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("power    NORMAL", out)


if __name__ == "__main__":
    unittest.main()
