# SPDX-License-Identifier: MIT
"""ugreen-dxp-pve-leds-bind.service: register the LED controller on its I2C bus."""
import argparse
import os
import sys
import time

from .config import OVERRIDES_PATH, ConfigError, add_config_option, load_config
from .sysfs import apply, write_attr

MODULE_NAME = "led-ugreen"
I2C_ADDR = "0x3a"
I2C_DEVICE = "{bus}-003a"  # sysfs name of the client at I2C_ADDR on bus N
ADAPTER_NAME = "SMBus I801 adapter"
WAIT_SECONDS = 120  # how long `bind` keeps retrying while the controller is not ready
RETRY_INTERVAL = 2


class BindError(Exception):
    pass


class NotReady(BindError):
    """The adapter or the controller is not available yet (e.g. early in boot)."""


def _read(path):
    with open(path) as f:
        return f.read().strip()


def _devices(sys_root):
    # Adapters (i2c-N) and clients (N-00aa) live here on every kernel; the legacy
    # /sys/class/i2c-adapter only exists with CONFIG_I2C_COMPAT.
    return os.path.join(sys_root, "bus", "i2c", "devices")


def list_adapters(sys_root="/sys"):
    """Return {bus_number: adapter_name} for every I2C adapter."""
    base = _devices(sys_root)
    try:
        entries = os.listdir(base)
    except FileNotFoundError:
        return {}
    adapters = {}
    for entry in entries:
        bus = entry.removeprefix("i2c-")
        if bus.isdigit():
            try:
                adapters[int(bus)] = _read(os.path.join(base, entry, "name"))
            except OSError:
                pass
    return adapters


def find_bus(adapters, override=None):
    """The configured bus, else the lowest-numbered SMBus I801 adapter, else None."""
    if override is not None:
        return override
    return next((bus for bus in sorted(adapters) if ADAPTER_NAME in adapters[bus]), None)


def _client(sys_root, bus):
    """Return (adapter_dir, client_dir) for the controller's address on `bus`."""
    adapter = os.path.join(_devices(sys_root), f"i2c-{bus}")
    if not os.path.isdir(adapter):
        raise NotReady(f"I2C adapter i2c-{bus} does not exist")
    return adapter, os.path.join(adapter, I2C_DEVICE.format(bus=bus))


def _name(client):
    return _read(os.path.join(client, "name")) if os.path.isdir(client) else None


def _attached(client):
    """True once the driver has probed the client successfully (it gets a `driver` link)."""
    return os.path.isdir(os.path.join(client, "driver"))


def bind(sys_root, bus, write=write_attr):
    adapter, client = _client(sys_root, bus)
    name = _name(client)
    if name not in (None, MODULE_NAME):
        raise BindError(f"{I2C_ADDR} on i2c-{bus} is already registered as {name}")
    if name == MODULE_NAME:
        if _attached(client):
            return f"{MODULE_NAME} already bound at {I2C_ADDR} on i2c-{bus}"
        # Registered, but its probe failed (the controller did not answer): probe again.
        write(os.path.join(adapter, "delete_device"), I2C_ADDR)
    write(os.path.join(adapter, "new_device"), f"{MODULE_NAME} {I2C_ADDR}")  # probes now
    if not _attached(client):
        raise NotReady(f"the LED controller at {I2C_ADDR} on i2c-{bus} did not answer")
    return f"Bound {MODULE_NAME} at {I2C_ADDR} on i2c-{bus}"


def unbind(sys_root, bus, write=write_attr):
    adapter, client = _client(sys_root, bus)
    name = _name(client)
    if name is None:
        return f"{MODULE_NAME} is not registered on i2c-{bus}"
    if name != MODULE_NAME:
        raise BindError(f"{I2C_ADDR} on i2c-{bus} is registered as {name}, not {MODULE_NAME}")
    write(os.path.join(adapter, "delete_device"), I2C_ADDR)
    return f"Unbound {MODULE_NAME} at {I2C_ADDR} from i2c-{bus}"


def reset_leds(config, write=write_attr):
    """Hand every LED back in the SHUTDOWN look, switched off.

    The controller keeps colours while the host is off and replays them in its
    own startup sequence, so this decides how the next boot looks. Best effort:
    an LED that cannot be written is skipped.
    """
    shutdown = config.states.get("SHUTDOWN")
    for led in config.leds.values():
        if not os.path.isdir(led.path):
            continue
        if shutdown:
            apply(led.path, shutdown, write=write)
        try:
            write(os.path.join(led.path, "brightness"), "0")  # off, keeping the colour
        except OSError:
            pass


def main(argv=None, write=write_attr, sleep=time.sleep, clock=time.monotonic):
    parser = argparse.ArgumentParser(description="Bind the UGREEN DXP LED controller")
    parser.add_argument("action", nargs="?", choices=("bind", "unbind"), default="bind")
    add_config_option(parser)
    parser.add_argument("--sys-root", default="/sys")
    parser.add_argument("--wait", type=float, default=WAIT_SECONDS, metavar="SECONDS",
                        help="bind: keep retrying this long while the adapter or the "
                             "controller is not ready (default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"invalid configuration: {e}", file=sys.stderr)
        return 1

    if args.action == "bind":
        action, deadline = bind, clock() + args.wait
    else:
        reset_leds(config, write)
        action, deadline = unbind, clock()
    waiting_for = None
    while True:
        try:
            bus = find_bus(list_adapters(args.sys_root), config.i2c_bus)
            if bus is None:
                raise NotReady(f"I2C adapter {ADAPTER_NAME!r} not found; "
                               f"set [bind] i2c_bus in {OVERRIDES_PATH}")
            print(action(args.sys_root, bus, write=write))
            return 0
        except NotReady as e:
            if clock() >= deadline:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            if str(e) != waiting_for:  # log each distinct reason once
                print(f"waiting: {e}", file=sys.stderr, flush=True)
                waiting_for = str(e)
            sleep(RETRY_INTERVAL)
        except (BindError, OSError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
