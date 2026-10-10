# SPDX-License-Identifier: MIT
"""Load and validate /etc/ugreen-dxp-pve-truenas.toml."""
import re
import tomllib
from dataclasses import dataclass, fields

from ugreen_leds.contribution import make_look

CONFIG_PATH = "/etc/ugreen-dxp-pve-truenas.toml"
# Every state the ZFS service can publish, most severe first.
DISK_STATES = ("FAULTED", "ERROR", "UNAVAIL", "REMOVED", "MISSING", "DEGRADED", "RESILVER",
               "OFFLINE", "CHECKING", "ONLINE_ALERT", "SPINDOWN", "ONLINE", "OFF")
POWER_STATES = ("FAULT",)  # published by the fan service
MAX_BAYS = 8  # disk1 ... disk8


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Fan:
    poll_interval: float = 30
    hdd_curve: tuple = ((30, 90), (35, 120), (40, 175), (43, 220), (45, 255))
    cpu_curve: tuple = ((45, 90), (55, 115), (65, 150), (75, 190), (85, 225), (95, 255))
    min_pwm: int = 90
    max_pwm: int = 255
    failsafe_pwm: int = 255
    manual_pwm_enable_value: int = 1
    auto_pwm_enable_value: int = 2
    reset_pwm_on_exit: bool = True
    auto_discover_hwmon: bool = True
    hwmon_regex: str = r"^it[0-9]+$"
    pwm_channel: int = 3
    temp_chip_regex: str = r"(?i)(drivetemp|nvme|ata|scsi|sas|sat|disk|hdd|ssd)"
    hwmon_name: str = ""
    pwm_path: str = ""
    pwm_enable_path: str = ""
    input_path: str = ""
    cpu_temp_path: str = ""


@dataclass(frozen=True)
class Zfs:
    poll_interval: float = 30
    alert_threshold: float = 0.75
    bays: tuple = tuple(f"/dev/disk/by-path/pci-0000:00:10.0-ata-{n}" for n in range(1, 5))


@dataclass(frozen=True)
class Config:
    vmid: str | None  # the TrueNAS VM ID as `qm` takes it; None until configured
    debug: bool
    fan: Fan
    zfs: Zfs
    power: dict  # state -> Look for the power LED
    disk: dict  # state -> Look for a disk LED


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int(low, high=None):
    def check(value):
        if not (isinstance(value, int) and not isinstance(value, bool)
                and value >= low and (high is None or value <= high)):
            limit = f"{low}-{high}" if high is not None else f">= {low}"
            raise ValueError(f"must be an integer {limit}, got {value!r}")
        return value
    return check


def _positive(value):
    if not (_is_number(value) and value > 0):
        raise ValueError(f"must be a positive number, got {value!r}")
    return value


def _fraction(value):
    if not (_is_number(value) and 0 <= value <= 1):
        raise ValueError(f"must be a number 0-1, got {value!r}")
    return value


def _bool(value):
    if not isinstance(value, bool):
        raise ValueError(f"must be true or false, got {value!r}")
    return value


def _str(value):
    if not isinstance(value, str):
        raise ValueError(f"must be a string, got {value!r}")
    return value


def _regex(value):
    try:
        re.compile(_str(value))
    except re.error as e:
        raise ValueError(f"invalid regular expression: {e}") from None
    return value


def _curve(value):
    """[[temp, pwm], ...] -> ((temp, pwm), ...) sorted by temperature."""
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError("must list at least two [temperature, pwm] points")
    points = []
    for point in value:
        if not (isinstance(point, list) and len(point) == 2 and _is_number(point[0])):
            raise ValueError(f"invalid point {point!r}: expected [temperature, pwm]")
        points.append((point[0], _int(0, 255)(point[1])))
    return tuple(sorted(points, key=lambda point: point[0]))


def _bays(value):
    if not (isinstance(value, list) and 1 <= len(value) <= MAX_BAYS
            and all(isinstance(path, str) and path.startswith("/") for path in value)):
        raise ValueError(f"must list 1-{MAX_BAYS} absolute paths, bay 1 first")
    return tuple(value)


FAN_CHECKS = {
    "poll_interval": _positive, "hdd_curve": _curve, "cpu_curve": _curve,
    "min_pwm": _int(0, 255), "max_pwm": _int(0, 255), "failsafe_pwm": _int(0, 255),
    "manual_pwm_enable_value": _int(0), "auto_pwm_enable_value": _int(0),
    "reset_pwm_on_exit": _bool, "auto_discover_hwmon": _bool, "hwmon_regex": _regex,
    "pwm_channel": _int(1), "temp_chip_regex": _regex, "hwmon_name": _str, "pwm_path": _str,
    "pwm_enable_path": _str, "input_path": _str, "cpu_temp_path": _str,
}
ZFS_CHECKS = {"poll_interval": _positive, "alert_threshold": _fraction, "bays": _bays}
assert set(FAN_CHECKS) == {f.name for f in fields(Fan)}
assert set(ZFS_CHECKS) == {f.name for f in fields(Zfs)}


def load(path=CONFIG_PATH):
    """Read and validate the TOML file at `path`."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError) as e:  # missing, unreadable, not UTF-8, or invalid TOML
        raise ConfigError(f"{path}: {e}") from None
    return build(data)


def build(data):
    """Validate parsed TOML data and return a Config."""
    _reject_unknown("top level", data, {"vmid", "debug", "fan", "zfs", "power", "disk"})
    vmid = data.get("vmid")
    if vmid is not None:
        vmid = str(_checked("vmid", _int(1), vmid))
    return Config(
        vmid=vmid,
        debug=_checked("debug", _bool, data.get("debug", False)),
        fan=_settings(Fan, "fan", _table(data, "fan"), FAN_CHECKS),
        zfs=_settings(Zfs, "zfs", _table(data, "zfs"), ZFS_CHECKS),
        power=_looks("power", _table(data, "power"), POWER_STATES),
        disk=_looks("disk", _table(data, "disk"), DISK_STATES),
    )


def _checked(where, check, value):
    try:
        return check(value)
    except ValueError as e:
        raise ConfigError(f"{where}: {e}") from None


def _settings(cls, where, table, checks):
    _reject_unknown(where, table, set(checks))
    return cls(**{key: _checked(f"{where}.{key}", checks[key], value)
                  for key, value in table.items()})


def _looks(kind, tables, required):
    _reject_unknown(f"[{kind}.*]", tables, set(required))
    looks = {}
    for name in required:
        table = tables.get(name)
        if not isinstance(table, dict):
            raise ConfigError(f"[{kind}.{name}] is required")
        _reject_unknown(f"[{kind}.{name}]", table, {"priority", "color", "effect"})
        try:
            looks[name] = make_look(table.get("priority"), table.get("color"),
                                    table.get("effect", "none"), name)
        except ValueError as e:
            raise ConfigError(f"{kind}.{name}: {e}") from None
    return looks


def _table(data, key):
    table = data.get(key, {})
    if not isinstance(table, dict):
        raise ConfigError(f"[{key}] must be a table")
    return table


def _reject_unknown(where, table, allowed):
    extra = sorted(set(table) - allowed)
    if extra:
        raise ConfigError(f"{where}: unknown keys {', '.join(extra)}")
