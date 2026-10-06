import os
import unittest
from unittest import mock

from tests.helpers import RuntimeDirectoryTestCase, load_script

fan = load_script("ugreen_truenas_fan", "src/fan/ugreen-truenas-fan.py")


class FanPowerFaultTest(RuntimeDirectoryTestCase):
    def setUp(self):
        super().setUp()
        fan.POWER_LED_FAULT_ACTIVE = None

    def test_fault_is_published_and_cleared(self):
        self.assertTrue(fan.set_power_led_fault(True))
        self.assertEqual(self.read("power"), "FAULT\n")
        self.assertTrue(fan.set_power_led_fault(False))
        self.assertEqual(os.listdir(self.dir), [])

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
        self.assertEqual(self.read("power"), "FAULT\n")


if __name__ == "__main__":
    unittest.main()
