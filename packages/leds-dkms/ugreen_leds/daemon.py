# SPDX-License-Identifier: MIT
"""ugreen-dxp-pve-leds.service: the only writer to the front-panel LEDs."""
import argparse
import json
import os
import signal
import sys

from . import sysfs
from .config import ConfigError, add_config_option, load_config
from .resolve import diff, resolve
from .tree import RUN_ROOT, producer_dirs, scan
from .watch import InotifyWatcher

STATUS_PATH = "/run/ugreen-dxp-pve-leds/status"
RESYNC_INTERVAL = 30  # seconds between passes when nothing changes


def log(message):
    print(message, file=sys.stderr, flush=True)


def _inode(path):
    """Identity of an LED device directory; changes when the driver recreates it."""
    try:
        return os.stat(path).st_ino
    except FileNotFoundError:
        return None


class Daemon:
    def __init__(self, config, run_root, status_path, watcher, log=log,
                 write_attr=sysfs.write_attr):
        self.config = config
        self.run_root = run_root
        self.status_path = status_path
        self.watcher = watcher
        self.log = log
        self.write_attr = write_attr
        self.painted = {}  # led -> (state name, inode of the LED directory when written)
        self._reported = set()

    def reload(self, config):
        """Switch to `config` and repaint every LED on the next pass."""
        self.config = config
        self.painted = {}

    def run_once(self):
        """Scan, resolve, write changed LEDs, refresh watches and the status file."""
        self.watcher.sync(producer_dirs(self.run_root))
        resolution = resolve(scan(self.run_root), self.config)
        # Forget LEDs whose device vanished or was recreated (driver reload, rebind).
        self.painted = {
            name: (state, ino) for name, (state, ino) in self.painted.items()
            if _inode(self.config.leds[name].path) == ino
        }
        errors = {}
        for led_name in diff(self.applied(), resolution.states):
            led = self.config.leds[led_name]
            state = self.config.states[resolution.states[led_name]]
            try:
                failures = sysfs.apply(led.path, state, write=self.write_attr)
            except sysfs.LedMissing:
                errors[led_name] = [f"LED path missing: {led.path}"]
                continue
            if failures:
                errors[led_name] = failures
                self.painted.pop(led_name, None)  # retry on the next pass
            else:
                self.painted[led_name] = (state.name, _inode(led.path))

        messages = list(resolution.problems)
        messages += [f"{led}: {error}" for led, errs in errors.items() for error in errs]
        try:
            write_status(self.status_path, resolution, errors, self.applied())
        except OSError as e:
            messages.append(f"cannot write status file {self.status_path}: {e}")
        self._report(messages)

    def applied(self):
        """{led: state name} for LEDs whose current state is on the hardware."""
        return {name: state for name, (state, _) in self.painted.items()}

    def _report(self, messages):
        """Log each message once, until it stops occurring."""
        for message in messages:
            if message not in self._reported:
                self.log(message)
        self._reported = set(messages)


def write_status(path, resolution, errors, applied):
    data = {
        "leds": {
            name: {
                "state": state,
                "applied": applied.get(name) == state,
                "contributors": [
                    {"producer": producer, "state": contributed}
                    for producer, contributed in resolution.contributors[name]
                ],
                "errors": errors.get(name, []),
            }
            for name, state in resolution.states.items()
        },
        "problems": resolution.problems,
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


class Signals:
    """Signal flags; handlers only set flags, the wakeup fd interrupts wait()."""

    def __init__(self):
        self.stop = False
        self.reload = False

    def install(self, wakeup_fd):
        signal.set_wakeup_fd(wakeup_fd)
        signal.signal(signal.SIGTERM, self._on_stop)
        signal.signal(signal.SIGINT, self._on_stop)
        signal.signal(signal.SIGHUP, self._on_reload)

    def _on_stop(self, _signum, _frame):
        self.stop = True

    def _on_reload(self, _signum, _frame):
        self.reload = True


def run(daemon, watcher, signals, load, interval=RESYNC_INTERVAL):
    """Main loop: one pass per wakeup, or every `interval` seconds, until stopped.

    The periodic pass paints LEDs that appeared late and repaints recreated ones.
    """
    while not signals.stop:
        if signals.reload:
            signals.reload = False
            try:
                daemon.reload(load())
            except ConfigError as e:
                daemon.log(f"reload failed, keeping the previous configuration: {e}")
            else:
                daemon.log("configuration reloaded")
        daemon.run_once()
        if not signals.stop:
            watcher.wait(timeout=interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Drive UGREEN DXP LEDs from published states")
    add_config_option(parser)
    parser.add_argument("--run-root", default=RUN_ROOT)
    parser.add_argument("--status-file", default=STATUS_PATH)
    args = parser.parse_args(argv)

    def load():
        return load_config(args.config)

    try:
        config = load()
    except ConfigError as e:
        log(f"invalid configuration: {e}")
        return 1

    os.makedirs(args.run_root, exist_ok=True)
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_r, False)
    os.set_blocking(wake_w, False)
    signals = Signals()
    signals.install(wake_w)

    watcher = InotifyWatcher(args.run_root, wakeup_fd=wake_r)
    daemon = Daemon(config, args.run_root, args.status_file, watcher)
    log(f"driving {len(config.leds)} LEDs from {args.run_root}")
    try:
        run(daemon, watcher, signals, load)
    finally:
        watcher.close()
    return 0
