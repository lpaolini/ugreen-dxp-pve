# SPDX-License-Identifier: MIT
"""ugreen-dxp-leds-migrate: create or convert /etc/ugreen-dxp-pve-leds.toml.

Run by postinst before the daemon (re)starts. It handles:
- a missing file (fresh install, or upgrade from 0.9.10): the template, with
  I2C_BUS and NETDEV_LED_DEVICE from the 0.9.10 shell config (`--legacy`);
- a dev-channel file of named-state overrides: converted in place, the old
  content kept as <file>.migrated;
- a current or unreadable file: left alone.
"""
import argparse
import os
import shutil
import sys
import tomllib

from .color import normalize_color
from .config import ConfigError, build
from .envfile import read_env
from .tomledit import set_key

NETDEV_KEYS = ("device_name", "color", "link", "tx", "rx", "interval")
POWER_LOOKS = ("NORMAL", "FAULT")
LEGACY_KEYS = {  # 0.9.10 variable -> (table, key, converter)
    "I2C_BUS": ("bind", "i2c_bus", int),
    "NETDEV_LED_DEVICE": ("netdev", "device_name", str),
}


def is_old_format(data):
    """True for the dev-channel overrides file: it has no [power.*] looks."""
    return "power" not in data or "states" in data or "leds" in data


def _hex(value):
    """'R G B' or '#rrggbb' -> '#rrggbb'."""
    return "#" + "".join(f"{int(part):02x}" for part in normalize_color(value).split())


def _effect(value):
    """Old blink_type ('none', 'blink 500 500') -> effect ('none', 'blink:500:500')."""
    return ":".join(str(value).split())


def convert_legacy(template, env):
    """Return (text, messages): the template with the 0.9.10 settings in `env`."""
    text, messages = template, []
    for name, (table, key, convert) in LEGACY_KEYS.items():
        if env.get(name):
            try:
                text = set_key(text, table, key, convert(env[name]))
            except ValueError as e:
                messages.append(f"ignored {name}={env[name]!r}: {e}")
    if any(name.endswith(("_COLOR", "_BLINK_TYPE")) for name in env):
        messages.append("LED colours and blink settings of the old configuration are not "
                        "migrated; set looks in the new file")
    return text, messages


def convert_overrides(template, old):
    """Return (text, messages): the template with the dev-channel overrides in `old`."""
    text, messages = template, []
    i2c_bus = old.get("bind", {}).get("i2c_bus")
    if i2c_bus is not None:
        text = set_key(text, "bind", "i2c_bus", i2c_bus)
    for name in old.get("leds", {}):
        messages.append(f"dropped [leds.{name}]: LED paths are fixed now")

    for name, table in old.get("states", {}).items():
        if name == "NETDEV":
            mapping = {key: ("netdev", key) for key in NETDEV_KEYS}
        elif name in POWER_LOOKS:
            mapping = {"color": (f"power.{name}", "color"),
                       "blink_type": (f"power.{name}", "effect")}
        elif name == "SHUTDOWN":
            mapping = {"color": ("shutdown", "color")}
        else:
            messages.append(f"[states.{name}] is not used any more; if it styled a disk "
                            f"state, set [disk.{name}] in /etc/ugreen-dxp-pve-truenas.toml")
            continue
        for key, value in table.items():
            if key not in mapping:
                note = ": the lowest priority wins now" if key == "priority" else ""
                messages.append(f"dropped states.{name}.{key}{note}")
                continue
            target, new_key = mapping[key]
            try:
                if new_key == "color":
                    value = _hex(value)
                elif new_key == "effect":
                    value = _effect(value)
            except ValueError as e:
                messages.append(f"dropped states.{name}.{key}: {e}")
                continue
            text = set_key(text, target, new_key, value)
    return text, messages


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ugreen-dxp-leds-migrate",
        description="Create or convert the LED configuration (run by postinst)")
    parser.add_argument("config")
    parser.add_argument("template")
    parser.add_argument("--legacy", help="0.9.10 /etc/ugreen-dxp-pve-leds.conf to read")
    args = parser.parse_args(argv)
    try:
        with open(args.template) as f:
            template = f.read()
    except OSError as e:
        print(f"cannot read the template: {e}", file=sys.stderr)
        return 1

    backup = None
    if os.path.exists(args.config):
        try:
            with open(args.config, "rb") as f:
                old = tomllib.load(f)
        except (OSError, ValueError) as e:
            print(f"leaving {args.config} as it is: {e}", file=sys.stderr)
            return 0
        if not is_old_format(old):
            return 0
        text, messages = convert_overrides(template, old)
        backup = f"{args.config}.migrated"
    elif args.legacy and os.path.exists(args.legacy):
        try:
            text, messages = convert_legacy(template, read_env(args.legacy))
        except (OSError, UnicodeDecodeError) as e:
            text, messages = template, [f"ignored {args.legacy}: {e}"]
        messages.append(f"Created {args.config} with the settings of {args.legacy}")
    else:
        text, messages = template, []

    try:
        build(tomllib.loads(text))
    except (ConfigError, ValueError) as e:
        print(f"cannot convert {args.config}: {e}", file=sys.stderr)
        return 1
    if backup:
        shutil.copy2(args.config, backup)
        messages.append(f"Converted {args.config} to the new format; "
                        f"the previous file is {backup}")
    tmp = f"{args.config}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, args.config)
    for message in messages:
        print(message)
    return 0
