import contextlib
import io
import os
import shutil
import tempfile
import unittest

from tests.helpers import make_led_dirs
from ugreen_leds.bind import BindError, NotReady, bind, find_bus, list_adapters, main, unbind
from ugreen_leds.sysfs import write_attr


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
    `write()` stands in for sysfs writes and reacts like the kernel: writing
    new_device registers a client and probes it at once; the driver attaches
    (a `driver` link appears) only if the controller answers.
    """

    def __init__(self, root):
        self.root = root
        self.answers = True
        self.writes = []

    def devices(self):
        return os.path.join(self.root, "bus", "i2c", "devices")

    def adapter(self, bus):
        return os.path.join(self.devices(), f"i2c-{bus}")

    def client(self, bus):
        return os.path.join(self.adapter(bus), f"{bus}-003a")

    def add_adapter(self, bus, name):
        os.makedirs(self.adapter(bus))
        with open(os.path.join(self.adapter(bus), "name"), "w") as f:
            f.write(name + "\n")

    def add_device(self, bus, name, attached=True):
        os.makedirs(self.client(bus))
        with open(os.path.join(self.client(bus), "name"), "w") as f:
            f.write(name + "\n")
        if attached:
            os.makedirs(os.path.join(self.client(bus), "driver"))
        os.symlink(self.client(bus), os.path.join(self.devices(), f"{bus}-003a"))

    def attached(self, bus):
        return os.path.isdir(os.path.join(self.client(bus), "driver"))

    def write(self, path, value):
        if not path.startswith(self.devices()):  # an LED attribute: a plain file write
            self.writes.append((os.path.relpath(path, self.root), value))
            write_attr(path, value)
            return
        adapter, control = os.path.split(path)
        bus = int(os.path.basename(adapter).removeprefix("i2c-"))
        self.writes.append((control, value))
        if control == "new_device":
            self.add_device(bus, value.split()[0], attached=self.answers)
        elif control == "delete_device":
            shutil.rmtree(self.client(bus))
            os.unlink(os.path.join(self.devices(), f"{bus}-003a"))


class BindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = FakeSys(self.tmp.name)
        # Adapter names as reported by a DXP4800 Plus on Proxmox VE 9.
        self.sys.add_adapter(0, "Synopsys DesignWare I2C adapter")
        self.sys.add_adapter(1, "SMBus I801 adapter at 0000:00:1f.4")

    def bind(self, bus=1):
        return bind(self.tmp.name, bus, write=self.sys.write)

    def unbind(self, bus=1):
        return unbind(self.tmp.name, bus, write=self.sys.write)

    def test_list_adapters_skips_clients(self):
        self.sys.add_device(1, "led-ugreen")
        self.assertEqual(list_adapters(self.tmp.name), {
            0: "Synopsys DesignWare I2C adapter",
            1: "SMBus I801 adapter at 0000:00:1f.4",
        })
        self.assertEqual(list_adapters(os.path.join(self.tmp.name, "missing")), {})

    def test_bind_registers_the_controller(self):
        self.assertIn("Bound", self.bind())
        self.assertEqual(self.sys.writes, [("new_device", "led-ugreen 0x3a")])
        self.assertTrue(self.sys.attached(1))

    def test_bind_is_idempotent(self):
        self.sys.add_device(1, "led-ugreen")
        self.assertIn("already bound", self.bind())
        self.assertEqual(self.sys.writes, [])

    def test_bind_retries_a_failed_probe(self):
        # Registered at boot while the controller did not answer: no driver attached.
        self.sys.add_device(1, "led-ugreen", attached=False)
        self.assertIn("Bound", self.bind())
        self.assertEqual(self.sys.writes, [("delete_device", "0x3a"),
                                           ("new_device", "led-ugreen 0x3a")])
        self.assertTrue(self.sys.attached(1))

    def test_bind_reports_a_controller_that_does_not_answer(self):
        self.sys.answers = False
        with self.assertRaisesRegex(NotReady, "did not answer"):
            self.bind()

    def test_bind_refuses_foreign_device(self):
        self.sys.add_device(1, "something-else")
        with self.assertRaises(BindError) as ctx:
            self.bind()
        self.assertNotIsInstance(ctx.exception, NotReady)
        self.assertEqual(self.sys.writes, [])

    def test_bind_missing_adapter_is_not_ready(self):
        with self.assertRaises(NotReady):
            self.bind(7)

    def test_unbind(self):
        self.assertIn("not registered", self.unbind())
        self.sys.add_device(1, "led-ugreen", attached=False)
        self.assertIn("Unbound", self.unbind())
        self.assertEqual(self.sys.writes, [("delete_device", "0x3a")])

    def test_unbind_refuses_foreign_device(self):
        self.sys.add_device(1, "something-else")
        with self.assertRaises(BindError):
            self.unbind()

    def config(self, text):
        """Write a config with `text`, a state N and (unless `text` has LEDs) one LED."""
        if "[leds." not in text:
            text += '\n[leds.power]\npath = "/x"\ndefault = "N"\n'
        path = os.path.join(self.tmp.name, "leds.toml")
        with open(path, "w") as f:
            f.write(text + "\n[states.N]\npriority = 0\n")
        return path

    def run_main(self, *args, on_sleep=None):
        now = [0.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
            if on_sleep:
                on_sleep()

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(list(args) + ["--sys-root", self.tmp.name],
                      write=self.sys.write, sleep=sleep, clock=lambda: now[0])
        return rc, out.getvalue(), err.getvalue(), sleeps

    def test_main_autodetects_bus(self):
        rc, out, _, _ = self.run_main("--config", self.config(""))
        self.assertEqual(rc, 0)
        self.assertIn("on i2c-1", out)

    def test_main_uses_configured_bus(self):
        rc, out, _, _ = self.run_main("bind", "--config", self.config("[bind]\ni2c_bus = 0"))
        self.assertEqual(rc, 0)
        self.assertIn("on i2c-0", out)

    def test_main_waits_until_the_controller_answers(self):
        self.sys.answers = False

        def controller_wakes_up():
            self.sys.answers = True

        rc, out, err, sleeps = self.run_main("--config", self.config(""),
                                             on_sleep=controller_wakes_up)
        self.assertEqual(rc, 0)
        self.assertEqual(len(sleeps), 1)
        self.assertIn("waiting: ", err)
        self.assertIn("Bound", out)
        self.assertTrue(self.sys.attached(1))

    def test_main_gives_up_after_the_wait(self):
        self.sys.answers = False
        rc, _, err, sleeps = self.run_main("--config", self.config(""), "--wait", "5")
        self.assertEqual(rc, 1)
        self.assertEqual(sum(sleeps), 6)  # retried every 2 s until past the deadline
        self.assertIn("ERROR: ", err)
        self.assertIn("did not answer", err)

    def test_main_reports_missing_adapter_without_waiting_when_asked(self):
        rc, _, err, sleeps = self.run_main("--config", self.config("[bind]\ni2c_bus = 9"),
                                           "--wait", "0")
        self.assertEqual((rc, sleeps), (1, []))
        self.assertIn("i2c-9 does not exist", err)

    def test_unbind_never_waits(self):
        rc, _, _, sleeps = self.run_main("unbind", "--config", self.config("[bind]\ni2c_bus = 9"))
        self.assertEqual((rc, sleeps), (1, []))

    def test_unbind_resets_every_led_before_releasing_the_controller(self):
        self.sys.add_device(1, "led-ugreen")
        make_led_dirs(os.path.join(self.tmp.name, "leds"), ["power", "disk1"])
        config = self.config(f"""
[leds.power]
path = "{self.tmp.name}/leds/power"
default = "N"
[leds.disk1]
path = "{self.tmp.name}/leds/disk1"
default = "N"
[leds.disk5]
path = "{self.tmp.name}/leds/disk5"
default = "N"
[states.SHUTDOWN]
priority = 0
trigger = "none"
color = "#ffffff"
""")
        rc, out, _, _ = self.run_main("unbind", "--config", config)
        self.assertEqual(rc, 0)
        self.assertIn("Unbound", out)
        self.assertEqual(self.sys.writes, [
            ("leds/power/trigger", "none"), ("leds/power/color", "255 255 255"),
            ("leds/power/brightness", "0"),
            ("leds/disk1/trigger", "none"), ("leds/disk1/color", "255 255 255"),
            ("leds/disk1/brightness", "0"),
            ("delete_device", "0x3a"),  # disk5 is absent and skipped
        ])

    def test_unbind_without_shutdown_state_only_switches_leds_off(self):
        self.sys.add_device(1, "led-ugreen")
        make_led_dirs(os.path.join(self.tmp.name, "leds"), ["power"])
        config = self.config(f"""
[leds.power]
path = "{self.tmp.name}/leds/power"
default = "N"
""")
        rc, _, _, _ = self.run_main("unbind", "--config", config)
        self.assertEqual(rc, 0)
        self.assertEqual(self.sys.writes, [("leds/power/brightness", "0"),
                                           ("delete_device", "0x3a")])

    def test_main_rejects_foreign_device_without_waiting(self):
        self.sys.add_device(1, "something-else")
        rc, _, err, sleeps = self.run_main("--config", self.config(""))
        self.assertEqual((rc, sleeps), (1, []))
        self.assertIn("registered as something-else", err)


if __name__ == "__main__":
    unittest.main()
