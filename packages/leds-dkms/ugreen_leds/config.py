# SPDX-License-Identifier: MIT
"""Load, merge and validate the LED configuration (TOML)."""
import re
import tomllib
from dataclasses import dataclass

from .color import normalize_color

OVERRIDES_PATH = "/etc/ugreen-dxp-pve-leds.toml"
DEFAULT_PATHS = ("/usr/share/ugreen-dxp-pve-leds/leds.toml", OVERRIDES_PATH)

# Used with fullmatch(): `$` would also accept a trailing newline.
_NAME = re.compile(r"[A-Za-z0-9_-]+")
_ATTR = re.compile(r"[a-z0-9_]+")
_BLINK = re.compile(r"none|(blink|breath) [0-9]+ [0-9]+")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Led:
    path: str
    default: str


@dataclass(frozen=True)
class State:
    name: str
    priority: int
    attrs: tuple  # ((attribute, value), ...) in write order, trigger first


@dataclass(frozen=True)
class Config:
    leds: dict  # name -> Led, in config order
    states: dict  # name -> State
    i2c_bus: int | None


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def deep_merge(base, override):
    """Return a new dict: `override` merged into `base`, recursing into tables.

    Keys keep their position from `base`; keys only in `override` are appended.
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def add_config_option(parser):
    parser.add_argument("--config", action="append",
                        help="config file, repeatable (default: package defaults + /etc)")


def load_config(paths=None):
    """Read every existing file in `paths` (default: DEFAULT_PATHS), merge in order, validate."""
    data = {}
    for path in paths or DEFAULT_PATHS:
        try:
            with open(path, "rb") as f:
                layer = tomllib.load(f)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:  # unreadable, not UTF-8, or invalid TOML
            raise ConfigError(f"{path}: {e}") from None
        data = deep_merge(data, layer)
    return build(data)


def build(data):
    """Validate merged TOML data and return a Config."""
    _reject_unknown("top level", data, {"bind", "leds", "states"})

    states = {
        name: _build_state(name, table)
        for name, table in _table(data, "states").items()
    }
    leds = {
        name: _build_led(name, table, states)
        for name, table in _table(data, "leds").items()
    }
    if not leds:
        raise ConfigError("no [leds.*] tables configured")

    bind = _table(data, "bind")
    _reject_unknown("bind", bind, {"i2c_bus"})
    i2c_bus = bind.get("i2c_bus")
    if i2c_bus is not None and not (_is_int(i2c_bus) and i2c_bus >= 0):
        raise ConfigError("bind.i2c_bus must be a non-negative integer")

    return Config(leds=leds, states=states, i2c_bus=i2c_bus)


def _table(data, key):
    table = data.get(key, {})
    if not isinstance(table, dict):
        raise ConfigError(f"[{key}] must be a table")
    return table


def _reject_unknown(where, table, allowed):
    extra = sorted(set(table) - allowed)
    if extra:
        raise ConfigError(f"{where}: unknown keys {', '.join(extra)}")


def _check_table(kind, name, table):
    if not _NAME.fullmatch(name):
        raise ConfigError(f"invalid {kind} name {name!r}: use letters, digits, '_' or '-'")
    if not isinstance(table, dict):
        raise ConfigError(f"{kind}s.{name} must be a table")


def _build_led(name, table, states):
    _check_table("led", name, table)
    path = table.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ConfigError(f"leds.{name}.path must be an absolute path")
    default = table.get("default")
    if default not in states:
        raise ConfigError(f"leds.{name}.default must name a configured state, got {default!r}")
    _reject_unknown(f"leds.{name}", table, {"path", "default"})
    return Led(path=path, default=default)


def _build_state(name, table):
    _check_table("state", name, table)
    priority = table.get("priority")
    if not _is_int(priority):
        raise ConfigError(f"states.{name}.priority must be an integer")

    attrs = []
    for key, value in table.items():
        if key == "priority":
            continue
        if not _ATTR.fullmatch(key):
            raise ConfigError(f"states.{name}: invalid attribute name {key!r}")
        if isinstance(value, bool):
            value = int(value)  # TOML true/false -> sysfs 1/0
        if not isinstance(value, (str, int)):
            raise ConfigError(f"states.{name}.{key} must be a string or an integer")
        if key == "color":
            if not isinstance(value, str):
                raise ConfigError(f"states.{name}.color must be a string")
            try:
                value = normalize_color(value)
            except ValueError as e:
                raise ConfigError(f"states.{name}.color: {e}") from None
        elif key == "blink_type" and not _BLINK.fullmatch(str(value)):
            raise ConfigError(
                f"states.{name}.blink_type must be 'none', 'blink ON OFF' or 'breath ON OFF'"
            )
        elif key == "brightness" and not (_is_int(value) and 0 <= value <= 255):
            raise ConfigError(f"states.{name}.brightness must be an integer 0-255")
        attrs.append((key, str(value)))

    attrs.sort(key=lambda item: item[0] != "trigger")  # stable: trigger first
    return State(name=name, priority=priority, attrs=tuple(attrs))
