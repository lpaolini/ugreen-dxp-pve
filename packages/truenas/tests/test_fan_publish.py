import os
import unittest
from unittest import mock

from tests.helpers import TEMPLATE, RuntimeDirectoryTestCase, load_script
from ugreen_truenas.config import load

fan = load_script("ugreen_truenas_fan", "src/fan/ugreen-truenas-fan.py")
FAULT = "priority=10 color=#ff0000 effect=blink:500:500 state=FAULT\n"


class FanPowerFaultTest(RuntimeDirectoryTestCase):
    def setUp(self):
        super().setUp()
        fan.configure(load(TEMPLATE))
        fan.POWER_LED_FAULT_ACTIVE = None

    def test_fault_is_published_and_cleared(self):
        self.assertTrue(fan.set_power_led_fault(True))
        self.assertEqual(self.read("power"), FAULT)
        self.assertTrue(fan.set_power_led_fault(False))
        self.assertEqual(os.listdir(self.dir), [])

    def test_outside_systemd_nothing_is_published(self):
        with mock.patch.dict(os.environ, {"RUNTIME_DIRECTORY": ""}):
            self.assertFalse(fan.set_power_led_fault(True))
        self.assertIsNone(fan.POWER_LED_FAULT_ACTIVE)

    def test_clear_without_fault_is_ok(self):
        self.assertTrue(fan.set_power_led_fault(False))
        self.assertEqual(os.listdir(self.dir), [])

    def test_unchanged_state_is_not_rewritten(self):
        fan.set_power_led_fault(True)
        os.unlink(os.path.join(self.dir, "power"))
        self.assertTrue(fan.set_power_led_fault(True))
        self.assertEqual(os.listdir(self.dir), [])

    def test_failure_is_retried_next_time(self):
        blocker = os.path.join(self.dir, "not-a-directory")
        open(blocker, "w").close()
        with mock.patch.dict(os.environ, {"RUNTIME_DIRECTORY": os.path.join(blocker, "fan")}):
            self.assertFalse(fan.set_power_led_fault(True))
        self.assertIsNone(fan.POWER_LED_FAULT_ACTIVE)
        os.unlink(blocker)
        self.assertTrue(fan.set_power_led_fault(True))
        self.assertEqual(self.read("power"), FAULT)


class FanStopTest(RuntimeDirectoryTestCase):
    def run_main(self, *args):
        bad = os.path.join(self.dir, "bad.toml")
        with open(bad, "w") as f:
            f.write("this is [not toml")
        argv = ["x", *args, "--config", bad]
        with mock.patch.object(fan.sys, "argv", argv), \
                mock.patch.object(fan, "log"), \
                mock.patch.object(fan, "resolve_host_hwmon_paths"), \
                mock.patch.object(fan, "set_fan_auto", return_value=True) as auto:
            with self.assertRaises(SystemExit) as cm:
                fan.main()
        return cm.exception.code, auto

    def test_stop_resets_the_fan_despite_an_invalid_config(self):
        code, auto = self.run_main("--stop")
        self.assertEqual(code, 0)
        auto.assert_called_once()

    def test_start_with_an_invalid_config_exits_1(self):
        code, auto = self.run_main("--start")
        self.assertEqual(code, 1)
        auto.assert_not_called()


class FanConfigureTest(unittest.TestCase):
    def test_settings_come_from_the_configuration(self):
        fan.configure(load(TEMPLATE))
        self.assertEqual(fan.HDD_CURVE[0], (30, 90))
        self.assertEqual(fan.pwm_for_temp(40, fan.HDD_CURVE), 175)
        self.assertEqual(fan.pwm_for_temp(20, fan.CPU_CURVE), 90)
        self.assertEqual(fan.FAN_PWM_CHANNEL, "3")
        self.assertEqual(fan.MANUAL_PWM_ENABLE_VALUE, "1")
        self.assertEqual(fan.POLL_INTERVAL, 30)
        self.assertTrue(fan.RESET_PWM_ON_EXIT)


if __name__ == "__main__":
    unittest.main()
