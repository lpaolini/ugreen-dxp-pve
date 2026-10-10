# SPDX-License-Identifier: MIT
"""Load and validate the LED configuration (/etc/ugreen-dxp-pve-leds.toml)."""
import os
import re
import tomllib
from dataclasses import dataclass

from .contribution import Look, make_look, rgb

CONFIG_PATH = "/etc/ugreen-dxp-pve-leds.toml"
LED_ROOT = "/sys/class/leds"
LED_NAMES = ("power", "netdev") + tuple(f"disk{n}" for n in range(1, 9))
CONTRIBUTED = tuple(name for name in LED_NAMES if name != "netdev")

# Used with fullmatch(): `$` would also accept a trailing newline.
_NAME = re.compile(r"[A-Za-z0-9_-]+")
_DEVICE = re.compile(r"[^\s/]{1,15}")  # a network interface name


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    i2c_bus: int | None
    netdev: tuple  # ((attribute, value), ...) for the network LED, in write order
    shutdown: Look  # shown on every LED when the controller is released
    power: dict  # name -> Look; NORMAL is the power LED while nothing is contributed
    led_root: str = LED_ROOT

    def led_path(self, name):
        return os.path.join(self.led_root, f"ugreen:white:{name}")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def add_config_option(parser):
    parser.add_argument("--config", default=CONFIG_PATH,
                        help="configuration file (default: %(default)s)")


def load_config(path=CONFIG_PATH, led_root=LED_ROOT):
    """Read and validate the TOML file at `path`."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError) as e:  # missing, unreadable, not UTF-8, or invalid TOML
        raise ConfigError(f"{path}: {e}") from None
    return build(data, led_root)


def build(data, led_root=LED_ROOT):
    """Validate parsed TOML data and return a Config."""
    _reject_unknown("top level", data, {"bind", "netdev", "shutdown", "power"})

    bind = _table(data, "bind")
    _reject_unknown("[bind]", bind, {"i2c_bus"})
    i2c_bus = bind.get("i2c_bus")
    if i2c_bus is not None and not (_is_int(i2c_bus) and i2c_bus >= 0):
        raise ConfigError("bind.i2c_bus must be a non-negative integer")

    shutdown = _table(data, "shutdown")
    _reject_unknown("[shutdown]", shutdown, {"color"})

    power = {}
    for name, table in _table(data, "power").items():
        if not _NAME.fullmatch(name):
            raise ConfigError(f"invalid power look name {name!r}: "
                              f"use letters, digits, '_' or '-'")
        if not isinstance(table, dict):
            raise ConfigError(f"power.{name} must be a table")
        _reject_unknown(f"[power.{name}]", table, {"priority", "color", "effect"})
        power[name] = _look(f"power.{name}", table.get("priority"), table.get("color"),
                            table.get("effect", "none"), state=name)
    if "NORMAL" not in power:
        raise ConfigError("[power.NORMAL] is required")

    return Config(i2c_bus=i2c_bus, netdev=_netdev(_table(data, "netdev")),
                  shutdown=_look("shutdown", 0, shutdown.get("color")),
                  power=power, led_root=led_root)


def _netdev(table):
    _reject_unknown("[netdev]", table, {"device_name", "color", "link", "tx", "rx", "interval"})
    device = table.get("device_name")
    if not (isinstance(device, str) and _DEVICE.fullmatch(device)):
        raise ConfigError(f"netdev.device_name must be a network interface name, got {device!r}")
    color = _look("netdev", 0, table.get("color")).color
    attrs = [("trigger", "netdev"), ("device_name", device), ("brightness", "255"),
             ("color", rgb(color))]
    for key in ("link", "tx", "rx"):
        value = table.get(key, 1)
        if isinstance(value, bool):
            value = int(value)  # TOML true/false -> sysfs 1/0
        if not (_is_int(value) and value in (0, 1)):
            raise ConfigError(f"netdev.{key} must be 0 or 1")
        attrs.append((key, str(value)))
    interval = table.get("interval", 50)
    if not (_is_int(interval) and interval > 0):
        raise ConfigError("netdev.interval must be a positive integer (milliseconds)")
    attrs.append(("interval", str(interval)))
    return tuple(attrs)


def _look(where, priority, color, effect="none", state=None):
    try:
        return make_look(priority, color, effect, state)
    except ValueError as e:
        raise ConfigError(f"{where}: {e}") from None


def _table(data, key):
    table = data.get(key, {})
    if not isinstance(table, dict):
        raise ConfigError(f"[{key}] must be a table")
    return table


def _reject_unknown(where, table, allowed):
    extra = sorted(set(table) - allowed)
    if extra:
        raise ConfigError(f"{where}: unknown keys {', '.join(extra)}")
