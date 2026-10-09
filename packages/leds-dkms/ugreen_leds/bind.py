# SPDX-License-Identifier: MIT
"""ugreen-dxp-pve-leds-bind.service: register the LED controller on its I2C bus."""
import argparse
import os
import sys

from .config import OVERRIDES_PATH, ConfigError, add_config_option, load_config
from .sysfs import write_attr

MODULE_NAME = "led-ugreen"
I2C_ADDR = "0x3a"
I2C_DEVICE = "{bus}-003a"  # sysfs name of the client at I2C_ADDR on bus N
ADAPTER_NAME = "SMBus I801 adapter"


class BindError(Exception):
    pass


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


def _registered(sys_root, bus):
    """Return (adapter_dir, name registered at I2C_ADDR or None)."""
    adapter = os.path.join(_devices(sys_root), f"i2c-{bus}")
    if not os.path.isdir(adapter):
        raise BindError(f"I2C adapter i2c-{bus} does not exist")
    device = os.path.join(adapter, I2C_DEVICE.format(bus=bus))
    name = _read(os.path.join(device, "name")) if os.path.isdir(device) else None
    return adapter, name


def bind(sys_root, bus):
    adapter, name = _registered(sys_root, bus)
    if name == MODULE_NAME:
        return f"{MODULE_NAME} already bound at {I2C_ADDR} on i2c-{bus}"
    if name is not None:
        raise BindError(f"{I2C_ADDR} on i2c-{bus} is already registered as {name}")
    write_attr(os.path.join(adapter, "new_device"), f"{MODULE_NAME} {I2C_ADDR}")
    return f"Bound {MODULE_NAME} at {I2C_ADDR} on i2c-{bus}"


def unbind(sys_root, bus):
    adapter, name = _registered(sys_root, bus)
    if name is None:
        return f"{MODULE_NAME} is not registered on i2c-{bus}"
    if name != MODULE_NAME:
        raise BindError(f"{I2C_ADDR} on i2c-{bus} is registered as {name}, not {MODULE_NAME}")
    write_attr(os.path.join(adapter, "delete_device"), I2C_ADDR)
    return f"Unbound {MODULE_NAME} at {I2C_ADDR} from i2c-{bus}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bind the UGREEN DXP LED controller")
    parser.add_argument("action", nargs="?", choices=("bind", "unbind"), default="bind")
    add_config_option(parser)
    parser.add_argument("--sys-root", default="/sys")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"invalid configuration: {e}", file=sys.stderr)
        return 1

    bus = find_bus(list_adapters(args.sys_root), config.i2c_bus)
    if bus is None:
        print(f"I2C adapter {ADAPTER_NAME!r} not found; set [bind] i2c_bus in {OVERRIDES_PATH}",
              file=sys.stderr)
        return 1

    action = bind if args.action == "bind" else unbind
    try:
        print(action(args.sys_root, bus))
    except (BindError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0
