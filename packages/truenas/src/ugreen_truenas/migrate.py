# SPDX-License-Identifier: MIT
"""ugreen-dxp-pve-truenas-migrate: build the TOML config from the old .conf files.

Run by postinst when /etc/ugreen-dxp-pve-truenas.toml does not exist yet.
"""
import argparse
import os
import sys
import tomllib

from ugreen_leds.envfile import read_env
from ugreen_leds.tomledit import set_key

from .config import ConfigError, build


def _number(text):
    value = float(text)
    return int(value) if value.is_integer() else value


def _curve(text):
    """'30:90,35:120' -> [[30, 90], [35, 120]]"""
    points = []
    for item in text.split(","):
        if item.strip():
            temp, pwm = item.split(":", 1)
            points.append([_number(temp), int(pwm)])
    return points


def _flag(text):
    return text == "1"  # as the old services: exactly "1" is true


FAN_KEYS = {  # old variable -> ([fan] key, converter)
    "POLL_INTERVAL": ("poll_interval", _number),
    "HDD_FAN_CURVE": ("hdd_curve", _curve),
    "CPU_FAN_CURVE": ("cpu_curve", _curve),
    "MIN_PWM": ("min_pwm", int),
    "MAX_PWM": ("max_pwm", int),
    "FAILSAFE_PWM": ("failsafe_pwm", int),
    "MANUAL_PWM_ENABLE_VALUE": ("manual_pwm_enable_value", int),
    "AUTO_PWM_ENABLE_VALUE": ("auto_pwm_enable_value", int),
    "RESET_PWM_ON_EXIT": ("reset_pwm_on_exit", _flag),
    "AUTO_DISCOVER_HWMON": ("auto_discover_hwmon", _flag),
    "FAN_HWMON_NAME": ("hwmon_name", str),
    "FAN_HWMON_REGEX": ("hwmon_regex", str),
    "FAN_PWM_CHANNEL": ("pwm_channel", int),
    "FAN_PWM_PATH": ("pwm_path", str),
    "FAN_PWM_ENABLE_PATH": ("pwm_enable_path", str),
    "FAN_INPUT_PATH": ("input_path", str),
    "CPU_TEMP_PATH": ("cpu_temp_path", str),
    "TEMP_CHIP_REGEX": ("temp_chip_regex", str),
}
ZFS_KEYS = {  # old variable -> ([zfs] key, converter)
    "POLL_INTERVAL": ("poll_interval", _number),
    "ALERT_THRESHOLD": ("alert_threshold", float),
}
BAY_KEYS = tuple(f"BAY_{n}_PATH" for n in range(1, 5))
SHARED_KEYS = ("VMID", "DEBUG")
# Settings of older releases that no longer mean anything: dropped silently.
OBSOLETE_KEYS = ("POWER_LED_HELPER", "POWER_LED_FAULT_NAME", "LEDS_OFF_ON_EXIT",
                 "LED_1_PATH", "LED_2_PATH", "LED_3_PATH", "LED_4_PATH")


def convert(template, fan, zfs):
    """Return (text, messages): `template` with the settings of the old files applied."""
    text, messages = template, []

    def apply(name, value, table, key, converted):
        """Set the key unless the result no longer validates; then keep the old text."""
        nonlocal text
        try:
            candidate = set_key(text, table, key, converted)
            build(tomllib.loads(candidate))
        except (ConfigError, ValueError, TypeError, KeyError) as e:
            messages.append(f"ignored {name}={value!r}: {e}")
            return False
        text = candidate
        return True

    def put(table, name, key, value, converter):
        try:
            converted = converter(value)
        except ValueError as e:
            messages.append(f"ignored {name}={value!r}: {e}")
            return False
        return apply(name, value, table, key, converted)

    vmids = {source: env["VMID"] for source, env in (("zfs", zfs), ("fan", fan))
             if env.get("VMID")}  # preferred first
    if len(set(vmids.values())) > 1:
        messages.append(f"VMID differs between the files (fan {vmids['fan']}, "
                        f"zfs {vmids['zfs']})")
    for source, vmid in vmids.items():
        if put(None, "VMID", "vmid", vmid, int):
            if len(set(vmids.values())) > 1:
                messages.append(f"using VMID {vmid} from the {source} file")
            break
    debug = [env["DEBUG"] for env in (fan, zfs) if "DEBUG" in env]
    if debug:
        apply("DEBUG", ",".join(debug), None, "debug", "1" in debug)

    for name, (key, converter) in FAN_KEYS.items():
        if fan.get(name):
            put("fan", name, key, fan[name], converter)
    for name, (key, converter) in ZFS_KEYS.items():
        if zfs.get(name):
            put("zfs", name, key, zfs[name], converter)
    bays = list(tomllib.loads(template)["zfs"]["bays"])
    wanted = {index: zfs[name] for index, name in enumerate(BAY_KEYS) if zfs.get(name)}
    if wanted:
        combined = [wanted.get(index, path) for index, path in enumerate(bays)]
        mark = len(messages)
        if not apply("BAY_n_PATH", wanted, "zfs", "bays", combined):
            del messages[mark:]  # retry bay by bay to keep the usable ones
            for index, path in wanted.items():
                candidate = bays.copy()
                candidate[index] = path
                if apply(BAY_KEYS[index], path, "zfs", "bays", candidate):
                    bays = candidate

    for which, env, known in (("fan", fan, FAN_KEYS), ("zfs", zfs, (*ZFS_KEYS, *BAY_KEYS))):
        for name in env:
            if name not in known and name not in SHARED_KEYS and name not in OBSOLETE_KEYS:
                messages.append(f"ignored unknown setting {name} in the {which} file")
    return text, messages


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ugreen-dxp-pve-truenas-migrate",
        description="Build the TrueNAS helper configuration from the old .conf files")
    parser.add_argument("--fan", help="old ugreen-dxp-pve-truenas-fan.conf")
    parser.add_argument("--zfs", help="old ugreen-dxp-pve-truenas-zfs.conf")
    parser.add_argument("template")
    parser.add_argument("output")
    args = parser.parse_args(argv)

    if os.path.exists(args.output):
        print(f"migration failed: {args.output} already exists", file=sys.stderr)
        return 1
    try:
        fan = read_env(args.fan) if args.fan else {}
        zfs = read_env(args.zfs) if args.zfs else {}
        with open(args.template) as f:
            template = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"migration failed: {e}", file=sys.stderr)
        return 1

    try:
        text, messages = convert(template, fan, zfs)
        build(tomllib.loads(text))  # safety net, e.g. for an invalid template
    except (ConfigError, ValueError, TypeError, KeyError) as e:
        print(f"migration failed: {e}", file=sys.stderr)
        return 1
    with open(args.output, "w") as f:
        f.write(text)
    os.chmod(args.output, 0o644)
    for message in messages:
        print(message)
    sources = ", ".join(path for path in (args.fan, args.zfs) if path)
    print(f"Created {args.output} from {sources or 'the template'}")
    return 0
