import contextlib
import io
import json
import os
import tempfile
import unittest

from ugreen_leds.cli import format_status, main

CONFIG = """
[netdev]
device_name = "eth0"
color = "#0040ff"

[shutdown]
color = "#ffffff"

[power.NORMAL]
priority = 100
color = "#004010"

[power.FAULT]
priority = 10
color = "#ff0000"
effect = "blink:500:500"
"""


def look(priority, color, effect="none", state=None):
    return {"priority": priority, "color": color, "effect": effect, "state": state}


class FormatStatusTest(unittest.TestCase):
    def test_format(self):
        fault = look(10, "#ff0000", "blink:500:500", "FAULT")
        online = look(70, "#002800", state="ONLINE")
        data = {
            "leds": {
                "power": {"look": fault, "source": "truenas-fan",
                          "contributors": [{"producer": "truenas-fan", **fault}],
                          "applied": True, "present": True, "errors": []},
                "netdev": {"device_name": "vmbr0", "applied": True, "present": True,
                           "errors": []},
                "disk1": {"look": online, "source": "truenas-zfs",
                          "contributors": [
                              {"producer": "manual", **look(80, "#ff0000", "blink:100:100")},
                              {"producer": "truenas-zfs", **online}],
                          "applied": False, "present": True,
                          "errors": ["/sys/class/leds/x/color='0 40 0': Input/output error"]},
                "disk5": {"look": look(999, "#000000", state="OFF"), "source": "default",
                          "contributors": [], "applied": False, "present": False, "errors": []},
            },
            "problems": ["truenas-zfs/disk9: unknown LED"],
        }
        self.assertEqual(format_status(data).splitlines(), [
            "power    FAULT          truenas-fan=FAULT",
            "netdev   vmbr0          (network traffic)",
            "disk1    ONLINE         manual=#ff0000/blink:100:100, truenas-zfs=ONLINE"
            "  [not applied]",
            "         error: /sys/class/leds/x/color='0 40 0': Input/output error",
            "disk5    OFF            (default)  [absent]",
            "problem: truenas-zfs/disk9: unknown LED",
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

    def manual_file(self, led):
        return os.path.join(self.run_root, "manual", led)

    def read(self, led):
        with open(self.manual_file(led)) as f:
            return f.read()

    def test_set_a_named_power_look_and_clear(self):
        self.assertEqual(self.run_cli("set", "power", "FAULT")[0], 0)
        self.assertEqual(self.read("power"),
                         "priority=10 color=#ff0000 effect=blink:500:500 state=FAULT\n")
        self.assertEqual(self.run_cli("clear", "power")[0], 0)
        self.assertFalse(os.path.exists(self.manual_file("power")))

    def test_set_raw_fields(self):
        self.assertEqual(self.run_cli("set", "disk1", "priority=5", "color=#00FF00")[0], 0)
        self.assertEqual(self.read("disk1"), "priority=5 color=#00ff00\n")

    def test_rejections(self):
        cases = {
            ("set", "netdev", "priority=1", "color=#000000"): "driven by the daemon",
            ("set", "disk9", "priority=1", "color=#000000"): "unknown LED",
            ("clear", "disk9"): "unknown LED",
            ("set", "power", "BOGUS"): "unknown power look 'BOGUS'; known: NORMAL, FAULT",
            ("set", "disk1", "FAULT"): "named looks exist only for the power LED",
            ("set", "disk1", "priority=1"): "invalid look: missing color",
        }
        for args, message in cases.items():
            with self.subTest(args=args):
                rc, _, err = self.run_cli(*args)
                self.assertEqual(rc, 2)
                self.assertIn(message, err)
        self.assertFalse(os.path.exists(self.run_root))

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
            json.dump({"leds": {"power": {
                "look": look(100, "#004010", state="NORMAL"), "source": "default",
                "contributors": [], "applied": True, "present": True, "errors": []}},
                "problems": []}, f)
        rc, out, _ = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("power    NORMAL         (default)", out)

    def test_status_of_another_shape_is_not_a_traceback(self):
        with open(os.path.join(self.tmp.name, "status"), "w") as f:
            json.dump({"leds": {"power": {}}, "problems": []}, f)
        rc, _, err = self.run_cli("status")
        self.assertEqual(rc, 1)
        self.assertIn("daemon status unavailable", err)


if __name__ == "__main__":
    unittest.main()
