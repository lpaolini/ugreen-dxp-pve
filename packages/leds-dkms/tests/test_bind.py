import contextlib
import io
import os
import tempfile
import unittest

from ugreen_leds.bind import BindError, bind, find_bus, list_adapters, main, unbind


class FindBusTest(unittest.TestCase):
    def test_override_wins(self):
        self.assertEqual(find_bus({0: "SMBus I801 adapter at efa0"}, override=3), 3)

    def test_lowest_matching_adapter(self):
        adapters = {5: "SMBus I801 adapter at f000", 0: "i915 gmbus dpc",
                    2: "SMBus I801 adapter at efa0"}
        self.assertEqual(find_bus(adapters), 2)

    def test_no_match(self):
        self.assertIsNone(find_bus({0: "i915 gmbus dpc"}))


class FakeSys:
    """A fake /sys with I2C adapters, laid out as on a current kernel.

    Adapters and clients both appear under /sys/bus/i2c/devices; the legacy
    /sys/class/i2c-adapter directory does not exist (no CONFIG_I2C_COMPAT).
    """

    def __init__(self, root):
        self.root = root

    def add_adapter(self, bus, name):
        os.makedirs(self.adapter(bus))
        with open(os.path.join(self.adapter(bus), "name"), "w") as f:
            f.write(name + "\n")
        for control in ("new_device", "delete_device"):
            open(os.path.join(self.adapter(bus), control), "w").close()

    def adapter(self, bus):
        return os.path.join(self.root, "bus", "i2c", "devices", f"i2c-{bus}")

    def add_device(self, bus, name):
        device = os.path.join(self.adapter(bus), f"{bus}-003a")
        os.makedirs(device)
        with open(os.path.join(device, "name"), "w") as f:
            f.write(name + "\n")
        # Clients are listed next to the adapters too.
        os.symlink(device, os.path.join(self.root, "bus", "i2c", "devices", f"{bus}-003a"))

    def read(self, bus, control):
        with open(os.path.join(self.adapter(bus), control)) as f:
            return f.read()


class BindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = FakeSys(self.tmp.name)
        # Adapter names as reported by a DXP4800 Plus on Proxmox VE 9.
        self.sys.add_adapter(0, "Synopsys DesignWare I2C adapter")
        self.sys.add_adapter(1, "SMBus I801 adapter at 0000:00:1f.4")

    def test_list_adapters_skips_clients(self):
        self.sys.add_device(1, "led-ugreen")
        self.assertEqual(list_adapters(self.tmp.name), {
            0: "Synopsys DesignWare I2C adapter",
            1: "SMBus I801 adapter at 0000:00:1f.4",
        })
        self.assertEqual(list_adapters(os.path.join(self.tmp.name, "missing")), {})

    def test_bind_writes_new_device(self):
        self.assertIn("Bound", bind(self.tmp.name, 1))
        self.assertEqual(self.sys.read(1, "new_device"), "led-ugreen 0x3a\n")

    def test_bind_is_idempotent(self):
        self.sys.add_device(1, "led-ugreen")
        self.assertIn("already bound", bind(self.tmp.name, 1))
        self.assertEqual(self.sys.read(1, "new_device"), "")

    def test_bind_refuses_foreign_device(self):
        self.sys.add_device(1, "something-else")
        with self.assertRaises(BindError):
            bind(self.tmp.name, 1)

    def test_bind_missing_adapter(self):
        with self.assertRaises(BindError):
            bind(self.tmp.name, 7)

    def test_unbind(self):
        self.assertIn("not registered", unbind(self.tmp.name, 1))
        self.sys.add_device(1, "led-ugreen")
        self.assertIn("Unbound", unbind(self.tmp.name, 1))
        self.assertEqual(self.sys.read(1, "delete_device"), "0x3a\n")

    def test_unbind_refuses_foreign_device(self):
        self.sys.add_device(1, "something-else")
        with self.assertRaises(BindError):
            unbind(self.tmp.name, 1)

    def config(self, text):
        path = os.path.join(self.tmp.name, "leds.toml")
        with open(path, "w") as f:
            f.write(text + '\n[leds.power]\npath = "/x"\ndefault = "N"\n'
                    '[states.N]\npriority = 0\n')
        return path

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(list(args) + ["--sys-root", self.tmp.name])
        return rc, out.getvalue(), err.getvalue()

    def test_main_autodetects_bus(self):
        rc, out, _ = self.run_main("--config", self.config(""))
        self.assertEqual(rc, 0)
        self.assertIn("on i2c-1", out)

    def test_main_uses_configured_bus(self):
        rc, out, _ = self.run_main("bind", "--config", self.config("[bind]\ni2c_bus = 0"))
        self.assertEqual(rc, 0)
        self.assertIn("on i2c-0", out)

    def test_main_reports_missing_adapter(self):
        rc, _, err = self.run_main("--config", self.config("[bind]\ni2c_bus = 9"))
        self.assertEqual(rc, 1)
        self.assertIn("i2c-9 does not exist", err)


if __name__ == "__main__":
    unittest.main()
