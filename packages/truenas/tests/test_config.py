import copy
import tomllib
import unittest

from tests.helpers import TEMPLATE
from ugreen_leds.contribution import Look
from ugreen_truenas.config import DISK_STATES, ConfigError, Cpu, Disk, Fan, Zfs, build, load


def template_data():
    with open(TEMPLATE, "rb") as f:
        return tomllib.load(f)


class TemplateTest(unittest.TestCase):
    def test_template_matches_the_defaults(self):
        config = load(TEMPLATE)
        self.assertEqual(config.vmid, "100")
        self.assertFalse(config.debug)
        self.assertEqual(config.fan, Fan())
        self.assertEqual(config.disk, Disk())
        self.assertEqual(config.cpu, Cpu())
        self.assertEqual(config.zfs, Zfs())

    def test_template_looks(self):
        config = load(TEMPLATE)
        self.assertEqual(list(config.disk_looks), list(DISK_STATES))
        self.assertEqual(config.disk_looks["DEGRADED"], Look(35, "#502800", "blink:500:500", "DEGRADED"))
        self.assertEqual(config.disk_looks["CHECKING"], Look(50, "#282828", "blink:100:100", "CHECKING"))
        self.assertEqual(config.disk_looks["OFF"], Look(90, "#000000", state="OFF"))
        self.assertEqual(config.power_looks, {
            "FAULT": Look(10, "#ff0000", "blink:500:500", "FAULT"),
            "TEMP_ALERT": Look(20, "#ff0000", state="TEMP_ALERT"),
            "TEMP_WARNING": Look(30, "#ff8000", state="TEMP_WARNING"),
        })
        priorities = [look.priority for look in config.disk_looks.values()]
        self.assertEqual(priorities, sorted(priorities))  # most severe first

    def test_template_fan(self):
        config = load(TEMPLATE)
        fan, disk, cpu = config.fan, config.disk, config.cpu
        self.assertEqual((fan.min_pwm, fan.max_pwm, fan.failsafe_pwm), (90, 255, 255))
        self.assertEqual(fan.pwm_channel, 3)
        self.assertEqual((disk.target_temperature, disk.ramp), (35, 17))
        self.assertEqual((disk.warning_temperature, disk.alert_temperature), (40, 43))
        self.assertEqual((cpu.target_temperature, cpu.ramp), (45, 3))


class BuildTest(unittest.TestCase):
    def build(self, change):
        data = template_data()
        change(data)
        return build(data)

    def test_vmid_and_settings(self):
        def change(data):
            data["vmid"] = 105
            data["disk"]["ramp"] = 20
            data["cpu"]["target_temperature"] = 50.5
            data["zfs"]["bays"] = ["/a", "/b"]

        config = self.build(change)
        self.assertEqual(config.vmid, "105")
        self.assertEqual((config.disk.ramp, config.cpu.target_temperature), (20, 50.5))
        self.assertEqual(config.zfs.bays, ("/a", "/b"))

    def test_missing_settings_use_the_defaults(self):
        def change(data):
            del data["vmid"]
            del data["fan"]["min_pwm"]
            del data["zfs"]
            del data["cpu"]
            for key in ("target_temperature", "ramp", "warning_temperature", "alert_temperature"):
                del data["disk"][key]

        config = self.build(change)
        self.assertEqual(config.vmid, "100")
        self.assertEqual(config.fan.min_pwm, 90)
        self.assertEqual(config.zfs, Zfs())
        self.assertEqual((config.disk, config.cpu), (Disk(), Cpu()))

    def test_invalid_configs(self):
        def setter(path, value):
            def change(data):
                *parents, key = path
                for parent in parents:
                    data = data[parent]
                if value is KeyError:
                    del data[key]
                else:
                    data[key] = value
            return change

        cases = {
            "missing disk state": (setter(("disk", "CHECKING"), KeyError), r"\[disk.CHECKING\] is required"),
            "missing power state": (setter(("power", "FAULT"), KeyError), r"\[power.FAULT\] is required"),
            "missing temperature look": (setter(("power", "TEMP_WARNING"), KeyError), r"\[power.TEMP_WARNING\] is required"),
            "warning not below alert": (setter(("disk", "warning_temperature"), 43), "disk.warning_temperature must be below disk.alert_temperature"),
            "bool temperature": (setter(("disk", "alert_temperature"), True), "disk.alert_temperature"),
            "nan temperature": (setter(("disk", "warning_temperature"), float("nan")), "disk.warning_temperature"),
            "unknown state": (setter(("power", "NORMAL"), {"priority": 1, "color": "#000000"}), "unknown keys NORMAL"),
            "unknown top-level key": (setter(("extra",), 1), "unknown keys extra"),
            "unknown fan key": (setter(("fan", "pwm"), 1), "fan: unknown keys pwm"),
            "rgb colour": (setter(("disk", "ONLINE", "color"), "0 40 0"), "disk.ONLINE: color must be"),
            "unknown look key": (setter(("disk", "ONLINE", "brightness"), 1), "unknown keys brightness"),
            "pwm out of range": (setter(("fan", "min_pwm"), 300), "fan.min_pwm"),
            "string vmid": (setter(("vmid",), "100"), "vmid"),
            "zero vmid": (setter(("vmid",), 0), "vmid"),
            "no bays": (setter(("zfs", "bays"), []), "zfs.bays"),
            "nine bays": (setter(("zfs", "bays"), ["/x"] * 9), "zfs.bays"),
            "relative bay": (setter(("zfs", "bays"), ["dev/x"]), "zfs.bays"),
            "threshold above 1": (setter(("zfs", "alert_threshold"), 1.5), "zfs.alert_threshold"),
            "zero interval": (setter(("zfs", "poll_interval"), 0), "zfs.poll_interval"),
            "nan fan interval": (setter(("fan", "poll_interval"), float("nan")), "fan.poll_interval"),
            "inf zfs interval": (setter(("zfs", "poll_interval"), float("inf")), "zfs.poll_interval"),
            "duplicate bays": (setter(("zfs", "bays"), ["/a", "/a"]), "zfs.bays"),
            "min above max pwm": (setter(("fan", "max_pwm"), 50), "fan.min_pwm must not exceed fan.max_pwm"),
            "bool pwm": (setter(("fan", "min_pwm"), True), "fan.min_pwm"),
            "debug string": (setter(("debug",), "yes"), "debug"),
            "zero ramp": (setter(("disk", "ramp"), 0), "disk.ramp"),
            "fractional ramp": (setter(("cpu", "ramp"), 3.3), "cpu.ramp"),
            "bool ramp": (setter(("cpu", "ramp"), True), "cpu.ramp"),
            "nan target": (setter(("disk", "target_temperature"), float("nan")), "disk.target_temperature"),
            "string target": (setter(("cpu", "target_temperature"), "45"), "cpu.target_temperature"),
            "warning on cpu": (setter(("cpu", "warning_temperature"), 80), "cpu: unknown keys warning_temperature"),
            "old fan key": (setter(("fan", "disk_ramp"), 17), "fan: unknown keys disk_ramp"),
            "unknown disk setting": (setter(("disk", "FAULTY"), 1), "disk: unknown keys FAULTY"),
            "unknown disk state": (setter(("disk", "online"), {"priority": 1, "color": "#000000"}), r"\[disk.\*\]: unknown keys online"),
            "zero channel": (setter(("fan", "pwm_channel"), 0), "fan.pwm_channel"),
            "retired curve": (setter(("fan", "hdd_curve"), [[30, 90], [45, 255]]), "fan: unknown keys hdd_curve"),
            "retired regex": (setter(("fan", "temp_chip_regex"), "x"), "fan: unknown keys temp_chip_regex"),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(ConfigError, message):
                self.build(change)

    def test_template_data_is_not_shared(self):
        data = template_data()
        build(copy.deepcopy(data))
        self.assertEqual(data, template_data())


class LoadTest(unittest.TestCase):
    def test_missing_file_names_it(self):
        with self.assertRaisesRegex(ConfigError, "/nonexistent.toml"):
            load("/nonexistent.toml")


if __name__ == "__main__":
    unittest.main()
