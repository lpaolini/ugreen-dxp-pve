# SPDX-License-Identifier: MIT
"""ugreen-dxp-pve-leds.service: the only writer to the front-panel LEDs."""
import argparse
import dataclasses
import json
import os
import signal
import sys

from . import sysfs
from .config import LED_NAMES, ConfigError, add_config_option, load_config
from .resolve import diff, resolve
from .tree import RUN_ROOT, STATUS_PATH, producer_dirs, scan
from .watch import InotifyWatcher

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
        self.painted = {}  # led -> (what was written, inode of the LED directory then)
        self._reported = set()
        self._status = None  # text of the status file as last written

    def reload(self, config):
        """Switch to `config` and repaint every LED on the next pass."""
        self.config = config
        self.painted = {}

    def run_once(self):
        """Scan, resolve, write changed LEDs, refresh watches and the status file."""
        os.makedirs(self.run_root, exist_ok=True)  # recreate it if removed at runtime
        self.watcher.sync(producer_dirs(self.run_root))
        resolution = resolve(scan(self.run_root), self.config)
        # The (attribute, value) writes per LED: only a change in these is repainted.
        wanted = {name: self.config.netdev if name == "netdev"
                  else sysfs.look_attrs(resolution.looks[name])
                  for name in LED_NAMES}
        # Forget LEDs whose device vanished or was recreated (driver reload, rebind).
        self.painted = {
            name: (shown, ino) for name, (shown, ino) in self.painted.items()
            if _inode(self.config.led_path(name)) == ino
        }
        errors, absent = {}, set()
        for led_name in diff(self.applied(), wanted):
            path = self.config.led_path(led_name)
            ino = _inode(path)  # before writing: a recreation mid-write must not count
            if ino is None:  # not bound yet, or not fitted (disk5-8 on a 4-bay model)
                absent.add(led_name)
                continue
            shown = wanted[led_name]
            failures = sysfs.apply(path, shown, write=self.write_attr)
            if failures:
                errors[led_name] = failures
                self.painted.pop(led_name, None)  # retry on the next pass
            else:
                self.painted[led_name] = (shown, ino)

        messages = list(resolution.problems)
        messages += [f"{led}: LED path missing: {self.config.led_path(led)}" for led in LED_NAMES if led in absent]
        messages += [f"{led}: {error}" for led, errs in errors.items() for error in errs]
        status = status_data(resolution, self.config, wanted, errors, self.applied(), absent)
        try:
            self._write_status(status)
        except OSError as e:
            messages.append(f"cannot write status file {self.status_path}: {e}")
        self._report(messages)

    def applied(self):
        """{led: what is on the hardware} for LEDs written successfully."""
        return {name: shown for name, (shown, _) in self.painted.items()}

    def _write_status(self, data):
        """Write the status file only when it changes: writing it wakes the root watch."""
        text = json.dumps(data, indent=2) + "\n"
        if text == self._status:
            return
        self._status = None  # unknown until the write succeeds
        tmp = f"{self.status_path}.tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, self.status_path)
        self._status = text

    def _report(self, messages):
        """Log each message once, until it stops occurring."""
        for message in messages:
            if message not in self._reported:
                self.log(message)
        self._reported = set(messages)


def status_data(resolution, config, wanted, errors, applied, absent):
    leds = {}
    for name in LED_NAMES:
        if name == "netdev":
            entry = {"device_name": dict(config.netdev)["device_name"]}
        else:
            entry = {
                "look": dataclasses.asdict(resolution.looks[name]),
                "source": resolution.sources[name],
                "contributors": [{"producer": producer, **dataclasses.asdict(look)}
                                 for producer, look in resolution.contributors[name]],
            }
        entry.update(applied=applied.get(name) == wanted[name], present=name not in absent,
                     errors=errors.get(name, []))
        leds[name] = entry
    return {"leds": leds, "problems": resolution.problems}


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


def run(daemon, signals, load, interval=RESYNC_INTERVAL):
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
            daemon.watcher.wait(timeout=interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Drive UGREEN DXP LEDs from published looks")
    add_config_option(parser)
    parser.add_argument("--run-root", default=RUN_ROOT)
    parser.add_argument("--status-file", default=STATUS_PATH)
    args = parser.parse_args(argv)

    # Install the handlers first: until then SIGHUP (a reload) would kill the process.
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_r, False)
    os.set_blocking(wake_w, False)
    signals = Signals()
    signals.install(wake_w)

    def load():
        return load_config(args.config)

    try:
        config = load()
    except ConfigError as e:
        log(f"invalid configuration: {e}")
        return 1

    watcher = InotifyWatcher(args.run_root, wakeup_fd=wake_r)
    daemon = Daemon(config, args.run_root, args.status_file, watcher)
    log(f"driving {len(LED_NAMES)} LEDs from {args.run_root}")
    try:
        run(daemon, signals, load)
    finally:
        watcher.close()
    return 0
