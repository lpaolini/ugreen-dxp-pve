import errno
import json
import os
import shutil
import sys
import tempfile
import unittest

from tests.helpers import led_dir, make_config, make_led_dirs
from ugreen_leds.config import LED_NAMES, ConfigError
from ugreen_leds.contribution import Look
from ugreen_leds.daemon import Daemon, Signals, run
from ugreen_leds.tree import publish

FAULT = "priority=10 color=#ff0000 effect=blink:500:500 state=FAULT"
ONLINE = "priority=70 color=#002800 state=ONLINE"
FAULTED = "priority=10 color=#500000 effect=blink:500:500 state=FAULTED"


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
        led = os.path.basename(os.path.dirname(path)).removeprefix("ugreen:white:")
        attr = os.path.basename(path)
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
        make_led_dirs(self.led_root, LED_NAMES)
        self.watcher = FakeWatcher()
        self.writer = RecordingWriter()
        self.logs = []
        self.daemon = Daemon(make_config(self.led_root), self.run_root, self.status_path,
                             self.watcher, log=self.logs.append, write_attr=self.writer)

    def contribute(self, producer, led, line):
        directory = os.path.join(self.run_root, producer)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, led), "w") as f:
            f.write(line + "\n")

    def status(self):
        with open(self.status_path) as f:
            return json.load(f)

    def writes(self, led):
        return [(attr, value) for name, attr, value in self.writer.calls if name == led]

    def written_leds(self):
        return {name for name, _, _ in self.writer.calls}

    def test_first_pass_paints_every_led(self):
        self.daemon.run_once()
        self.assertEqual(self.written_leds(), set(LED_NAMES))
        self.assertEqual(self.writes("power"), [("trigger", "none"), ("color", "0 64 16"),
                                                ("blink_type", "none"), ("brightness", "255")])
        self.assertEqual(self.writes("disk1"), [("trigger", "none"), ("color", "0 0 0"),
                                                ("blink_type", "none"), ("brightness", "1")])
        self.assertEqual(self.writes("netdev"), [
            ("trigger", "netdev"), ("device_name", "eth0"), ("brightness", "255"),
            ("color", "0 64 255"), ("link", "1"), ("tx", "1"), ("rx", "1"), ("interval", "50")])
        status = self.status()["leds"]
        self.assertEqual(status["power"], {
            "look": {"priority": 100, "color": "#004010", "effect": "none", "state": "NORMAL"},
            "source": "default", "contributors": [],
            "applied": True, "present": True, "errors": []})
        self.assertEqual(status["netdev"], {"device_name": "eth0", "applied": True,
                                            "present": True, "errors": []})

    def test_only_changed_leds_are_written(self):
        self.daemon.run_once()
        self.writer.calls.clear()
        self.contribute("fan", "power", FAULT)
        self.daemon.run_once()
        self.assertEqual(self.written_leds(), {"power"})
        self.assertEqual(self.writes("power"), [("trigger", "none"), ("color", "255 0 0"),
                                                ("blink_type", "blink 500 500"),
                                                ("brightness", "255")])
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual(self.writer.calls, [])

    def test_label_only_change_is_not_rewritten(self):
        self.contribute("zfs", "disk1", "priority=70 color=#002800 effect=blink:500:500 state=OFFLINE")
        self.daemon.run_once()
        self.writer.calls.clear()
        self.contribute("zfs", "disk1", "priority=60 color=#002800 effect=blink:500:500 state=REMOVED")
        self.daemon.run_once()
        self.assertEqual(self.writer.calls, [])
        disk1 = self.status()["leds"]["disk1"]
        self.assertEqual(disk1["look"]["state"], "REMOVED")
        self.assertTrue(disk1["applied"])

    def test_network_led_is_written_again_only_after_a_reload(self):
        self.daemon.run_once()
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual(self.writes("netdev"), [])
        self.daemon.reload(make_config(self.led_root))
        self.daemon.run_once()
        self.assertEqual(len(self.writes("netdev")), 8)

    def test_watches_follow_producer_directories(self):
        self.contribute("truenas-zfs", "disk1", ONLINE)
        self.daemon.run_once()
        self.assertEqual(self.watcher.synced[-1], [os.path.join(self.run_root, "truenas-zfs")])

    def test_status_lists_contributors(self):
        self.contribute("truenas-zfs", "disk1", ONLINE)
        self.contribute("smart", "disk1", FAULTED)
        self.daemon.run_once()
        disk1 = self.status()["leds"]["disk1"]
        self.assertEqual(disk1["source"], "smart")
        self.assertEqual(disk1["look"]["state"], "FAULTED")
        self.assertEqual(disk1["contributors"], [
            {"producer": "smart", "priority": 10, "color": "#500000",
             "effect": "blink:500:500", "state": "FAULTED"},
            {"producer": "truenas-zfs", "priority": 70, "color": "#002800",
             "effect": "none", "state": "ONLINE"},
        ])

    def test_status_file_is_rewritten_only_when_it_changes(self):
        self.daemon.run_once()
        first = os.stat(self.status_path).st_ino
        self.daemon.run_once()
        self.assertEqual(os.stat(self.status_path).st_ino, first)
        self.contribute("fan", "power", FAULT)
        self.daemon.run_once()
        self.assertNotEqual(os.stat(self.status_path).st_ino, first)

    def test_failed_write_is_retried_and_logged_once(self):
        self.writer.fail.add(("power", "color"))
        self.daemon.run_once()
        self.daemon.run_once()
        self.assertEqual(self.writer.calls.count(("power", "color", "0 64 16")), 2)
        self.assertEqual(len([m for m in self.logs if m.startswith("power:")]), 1)
        status = self.status()["leds"]["power"]
        self.assertFalse(status["applied"])
        self.assertIn("Input/output error", status["errors"][0])

        self.writer.fail.clear()
        self.daemon.run_once()
        self.assertTrue(self.status()["leds"]["power"]["applied"])
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual(self.writer.calls, [])

    def test_missing_led_is_logged_once_and_painted_when_it_appears(self):
        shutil.rmtree(led_dir(self.led_root, "disk5"))
        self.daemon.run_once()
        self.daemon.run_once()
        missing = [m for m in self.logs if "disk5" in m]
        self.assertEqual(len(missing), 1)
        self.assertIn("LED path missing", missing[0])
        disk5 = self.status()["leds"]["disk5"]
        self.assertEqual((disk5["applied"], disk5["present"], disk5["errors"]), (False, False, []))

        make_led_dirs(self.led_root, ["disk5"])  # e.g. the driver bound late
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual(self.written_leds(), {"disk5"})
        self.assertTrue(self.status()["leds"]["disk5"]["applied"])

    def recreate_led(self, name):
        """Replace an LED directory with a new inode, as a driver reload does."""
        fresh = os.path.join(self.tmp.name, "fresh")
        make_led_dirs(fresh, [name])
        shutil.rmtree(led_dir(self.led_root, name))
        os.rename(led_dir(fresh, name), led_dir(self.led_root, name))

    def test_recreated_led_is_repainted(self):
        self.daemon.run_once()
        self.recreate_led("power")
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual(self.written_leds(), {"power"})

    def test_led_recreated_while_writing_is_repainted(self):
        def recreate_once(led, attr):
            if (led, attr) == ("power", "trigger"):
                self.writer.on_write = None
                self.recreate_led("power")

        self.writer.on_write = recreate_once
        self.daemon.run_once()
        self.writer.calls.clear()
        self.daemon.run_once()
        self.assertEqual(self.written_leds(), {"power"})

    def test_run_root_removed_at_runtime_is_recreated(self):
        shutil.rmtree(self.run_root)
        self.daemon.run_once()
        self.assertTrue(os.path.isdir(self.run_root))

    def test_problems_are_logged_once(self):
        self.contribute("truenas-zfs", "disk1", "BOGUS")
        self.daemon.run_once()
        self.daemon.run_once()
        problem = "truenas-zfs/disk1: expected key=value, got 'BOGUS'"
        self.assertEqual(self.logs, [problem])
        self.assertEqual(self.status()["problems"], [problem])

    def test_reload_repaints_everything(self):
        self.daemon.run_once()
        self.writer.calls.clear()
        self.daemon.reload(make_config(self.led_root))
        self.daemon.run_once()
        self.assertEqual(self.written_leds(), set(LED_NAMES))


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
    def setUp(self):
        from ugreen_leds.watch import InotifyWatcher

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_root = os.path.join(self.tmp.name, "run")
        self.led_root = os.path.join(self.tmp.name, "leds")
        os.makedirs(self.run_root)
        make_led_dirs(self.led_root, ["power", "disk1"])
        self.watcher = InotifyWatcher(self.run_root)
        self.addCleanup(self.watcher.close)

    def daemon(self, status_path):
        return Daemon(make_config(self.led_root), self.run_root, status_path, self.watcher,
                      log=lambda message: None)

    def color(self):
        with open(os.path.join(led_dir(self.led_root, "power"), "color")) as f:
            return f.read()

    def test_publish_and_producer_exit_repaint_the_led(self):
        daemon = self.daemon(os.path.join(self.tmp.name, "status"))
        daemon.run_once()
        self.assertEqual(self.color(), "0 64 16\n")

        fan = os.path.join(self.run_root, "fan")
        os.makedirs(fan)
        self.assertTrue(self.watcher.wait(timeout=1))
        daemon.run_once()
        publish(fan, "power", Look(10, "#ff0000", "blink:500:500", "FAULT"))
        self.assertTrue(self.watcher.wait(timeout=1))
        daemon.run_once()
        self.assertEqual(self.color(), "255 0 0\n")

        shutil.rmtree(fan)  # what systemd does when the producer stops
        self.assertTrue(self.watcher.wait(timeout=1))
        daemon.run_once()
        self.assertEqual(self.color(), "0 64 16\n")

    def test_status_file_in_the_tree_does_not_keep_waking_the_daemon(self):
        daemon = self.daemon(os.path.join(self.run_root, ".status"))
        daemon.run_once()  # writes .status: the root watch reports it
        self.assertTrue(self.watcher.wait(timeout=1))
        daemon.run_once()  # nothing changed, so nothing is written
        self.assertFalse(self.watcher.wait(timeout=0.2))


if __name__ == "__main__":
    unittest.main()
