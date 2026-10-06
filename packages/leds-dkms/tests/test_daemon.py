import errno
import json
import os
import shutil
import sys
import tempfile
import unittest

from tests.helpers import make_config, make_led_dirs
from ugreen_leds.config import ConfigError
from ugreen_leds.daemon import Daemon, Signals, run
from ugreen_leds.tree import publish


class FakeWatcher:
    def __init__(self, steps=()):
        self.synced = []
        self.timeouts = []
        self.steps = list(steps)

    def sync(self, dirs):
        self.synced.append(list(dirs))

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        self.steps.pop(0)()
        return True


class RecordingWriter:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.on_write = None  # optional hook(led, attr)

    def __call__(self, path, value):
        led, attr = path.split(os.sep)[-2:]
        self.calls.append((led, attr, value))
        if self.on_write:
            self.on_write(led, attr)
        if (led, attr) in self.fail:
            raise OSError(errno.EIO, "Input/output error")


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_root = os.path.join(self.tmp.name, "run")
        self.led_root = os.path.join(self.tmp.name, "leds")
        self.status_path = os.path.join(self.tmp.name, "status")
        os.makedirs(self.run_root)
        make_led_dirs(self.led_root, ["power", "disk1"])
        self.watcher = FakeWatcher()
        self.writer = RecordingWriter()
        self.logs = []
        self.daemon = Daemon(make_config(self.led_root), self.run_root, self.status_path,
                             self.watcher, log=self.logs.append, write_attr=self.writer)

    def publish(self, producer, led, state):
        directory = os.path.join(self.run_root, producer)
        os.makedirs(directory, exist_ok=True)
        publish(directory, led, state)

    def status(self):
        with open(self.status_path) as f:
            return json.load(f)

    def test_first_pass_paints_defaults(self):
        self.daemon.run_once()
        self.assertEqual(self.writer.calls, [
            ("power", "trigger", "none"), ("power", "color", "0 64 16"),
            ("disk1", "color", "0 0 0"),
        ])
        self.assertEqual(self.status()["leds"]["power"],
                         {"state": "NORMAL", "applied": True, "contributors": [], "errors": []})

    def test_only_changed_leds_are_written(self):
        self.daemon.run_once()
        self.writer.calls.clear()
        self.publish("fan", "power", "FAULT")
        self.daemon.run_once()
        self.assertEqual([c[0] for c in self.writer.calls], ["power"] * 3)
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual(self.writer.calls, [])

    def test_watches_follow_producer_directories(self):
        self.publish("zfs", "disk1", "ONLINE")
        self.daemon.run_once()
        self.assertEqual(self.watcher.synced[-1], [os.path.join(self.run_root, "zfs")])

    def test_status_lists_contributors(self):
        self.publish("zfs", "disk1", "ONLINE")
        self.publish("smart", "disk1", "FAULTED")
        self.daemon.run_once()
        self.assertEqual(self.status()["leds"]["disk1"]["contributors"], [
            {"producer": "smart", "state": "FAULTED"},
            {"producer": "zfs", "state": "ONLINE"},
        ])
        self.assertEqual(self.status()["leds"]["disk1"]["state"], "FAULTED")

    def test_failed_write_is_retried_and_logged_once(self):
        self.writer.fail.add(("power", "color"))
        self.daemon.run_once()
        self.daemon.run_once()
        self.assertEqual(self.writer.calls.count(("power", "color", "0 64 16")), 2)
        self.assertEqual(len([m for m in self.logs if "power" in m]), 1)
        status = self.status()["leds"]["power"]
        self.assertFalse(status["applied"])
        self.assertIn("Input/output error", status["errors"][0])

        self.writer.fail.clear()
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertTrue(self.status()["leds"]["power"]["applied"])
        self.daemon.run_once()
        self.assertNotIn("power", [c[0] for c in self.writer.calls[2:]])

    def test_missing_led_is_logged_once_and_painted_when_it_appears(self):
        config = make_config(self.led_root, leds=("power", "disk1", "disk5"))
        self.daemon.reload(config)
        self.daemon.run_once()
        self.daemon.run_once()
        missing = [m for m in self.logs if "disk5" in m]
        self.assertEqual(len(missing), 1)
        self.assertIn("LED path missing", missing[0])
        self.assertFalse(self.status()["leds"]["disk5"]["applied"])

        make_led_dirs(self.led_root, ["disk5"])  # e.g. the driver bound late
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual(self.writer.calls, [("disk5", "color", "0 0 0")])
        self.assertTrue(self.status()["leds"]["disk5"]["applied"])

    def recreate_led(self, name):
        """Replace an LED directory with a new inode, as a driver reload does."""
        fresh = os.path.join(self.led_root, f"{name}.new")
        make_led_dirs(self.led_root, [f"{name}.new"])
        shutil.rmtree(os.path.join(self.led_root, name))
        os.rename(fresh, os.path.join(self.led_root, name))

    def test_recreated_led_is_repainted(self):
        self.daemon.run_once()
        self.recreate_led("power")
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual([c[0] for c in self.writer.calls], ["power", "power"])

    def test_led_recreated_while_writing_is_repainted(self):
        def recreate_once(led, attr):
            if (led, attr) == ("power", "trigger"):
                self.writer.on_write = None
                self.recreate_led("power")

        self.writer.on_write = recreate_once
        self.daemon.run_once()
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual([c[0] for c in self.writer.calls], ["power", "power"])

    def test_run_root_removed_at_runtime_is_recreated(self):
        shutil.rmtree(self.run_root)
        self.daemon.run_once()
        self.assertTrue(os.path.isdir(self.run_root))

    def test_problems_are_logged_once(self):
        self.publish("zfs", "disk1", "BOGUS")
        self.daemon.run_once()
        self.daemon.run_once()
        self.assertEqual(self.logs, ["zfs/disk1: unknown state 'BOGUS'"])
        self.assertEqual(self.status()["problems"], ["zfs/disk1: unknown state 'BOGUS'"])

    def test_reload_repaints_everything(self):
        self.daemon.run_once()
        self.writer.calls.clear()
        self.daemon.reload(make_config(self.led_root))
        self.daemon.run_once()
        self.assertEqual(sorted({c[0] for c in self.writer.calls}), ["disk1", "power"])


class RunLoopTest(unittest.TestCase):
    def test_reload_and_stop(self):
        signals = Signals()
        passes, logs, configs = [], [], []

        class FakeDaemon:
            def run_once(self):
                passes.append(1)

            def reload(self, config):
                configs.append(config)

            def log(self, message):
                logs.append(message)

        def request_reload():
            signals.reload = True

        def request_stop():
            signals.stop = True

        loads = iter([ConfigError("bad"), "new-config"])

        def load():
            value = next(loads)
            if isinstance(value, Exception):
                raise value
            return value

        daemon = FakeDaemon()
        daemon.watcher = FakeWatcher([request_reload, request_reload, request_stop])
        run(daemon, signals, load, interval=7)
        self.assertEqual(len(passes), 3)
        self.assertEqual(daemon.watcher.timeouts, [7, 7, 7])
        self.assertEqual(configs, ["new-config"])
        self.assertEqual(logs, ["reload failed, keeping the previous configuration: bad",
                                "configuration reloaded"])


@unittest.skipUnless(sys.platform.startswith("linux"), "inotify is Linux-only")
class InotifyIntegrationTest(unittest.TestCase):
    def test_publish_and_producer_exit_repaint_the_led(self):
        from ugreen_leds.watch import InotifyWatcher

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_root = os.path.join(tmp.name, "run")
        led_root = os.path.join(tmp.name, "leds")
        os.makedirs(run_root)
        make_led_dirs(led_root, ["power", "disk1"])
        watcher = InotifyWatcher(run_root)
        self.addCleanup(watcher.close)
        daemon = Daemon(make_config(led_root), run_root, os.path.join(tmp.name, "status"),
                        watcher, log=lambda message: None)

        def color():
            with open(os.path.join(led_root, "power", "color")) as f:
                return f.read()

        daemon.run_once()
        self.assertEqual(color(), "0 64 16\n")

        fan = os.path.join(run_root, "fan")
        os.makedirs(fan)
        self.assertTrue(watcher.wait(timeout=1))
        daemon.run_once()
        publish(fan, "power", "FAULT")
        self.assertTrue(watcher.wait(timeout=1))
        daemon.run_once()
        self.assertEqual(color(), "255 0 0\n")

        shutil.rmtree(fan)  # what systemd does when the producer stops
        self.assertTrue(watcher.wait(timeout=1))
        daemon.run_once()
        self.assertEqual(color(), "0 64 16\n")


if __name__ == "__main__":
    unittest.main()
