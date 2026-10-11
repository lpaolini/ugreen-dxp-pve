# SPDX-License-Identifier: MIT
"""Load and validate /etc/ugreen-dxp-pve-truenas.toml."""
import math
import tomllib
from dataclasses import dataclass, fields

from ugreen_leds.contribution import make_look

CONFIG_PATH = "/etc/ugreen-dxp-pve-truenas.toml"
# Every state the ZFS service can publish, most severe first.
DISK_STATES = ("FAULTED", "ERROR", "UNAVAIL", "REMOVED", "MISSING", "DEGRADED", "RESILVER",
               "OFFLINE", "CHECKING", "ONLINE_ALERT", "SPINDOWN", "ONLINE", "OFF")
POWER_STATES = ("FAULT", "TEMP_ALERT", "TEMP_WARNING")  # published by the fan service
MAX_BAYS = 8  # disk1 ... disk8
DEFAULT_VMID = 100


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Fan:
    poll_interval: float = 30
    min_pwm: int = 90
    max_pwm: int = 255
    failsafe_pwm: int = 255
    pwm_channel: int = 3


# For each sensor the fan stays at min_pwm up to target_temperature (°C) and
# adds `ramp` PWM per °C above it.
@dataclass(frozen=True)
class Disk:
    target_temperature: float = 35
    ramp: int = 17
    warning_temperature: float = 40  # the power LED turns orange
    alert_temperature: float = 43  # the power LED turns red and the fan runs at max_pwm


@dataclass(frozen=True)
class Cpu:
    target_temperature: float = 45
    ramp: int = 3


@dataclass(frozen=True)
class Zfs:
    poll_interval: float = 30
    alert_threshold: float = 0.75
    bays: tuple = ()  # disk path inside TrueNAS per bay; empty: detect the bays


@dataclass(frozen=True)
class Config:
    vmid: str  # the TrueNAS VM ID as `qm` takes it
    debug: bool
    fan: Fan
    disk: Disk
    cpu: Cpu
    zfs: Zfs
    power_looks: dict  # state -> Look for the power LED
    disk_looks: dict  # state -> Look for a disk LED


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


def _temperature(value):
    if not (_is_number(value) and math.isfinite(value)):
        raise ValueError(f"must be a temperature in °C, got {value!r}")
    return value


def _positive(value):
    if not (_is_number(value) and math.isfinite(value) and value > 0):
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


def _bays(value):
    if not (isinstance(value, list) and 1 <= len(value) <= MAX_BAYS
            and all(isinstance(path, str) and path.startswith("/") for path in value)
            and len(set(value)) == len(value)):
        raise ValueError(f"must list 1-{MAX_BAYS} distinct absolute paths, bay 1 first")
    return tuple(value)


FAN_CHECKS = {
    "poll_interval": _positive, "min_pwm": _int(0, 255), "max_pwm": _int(0, 255),
    "failsafe_pwm": _int(0, 255), "pwm_channel": _int(1),
}
CPU_CHECKS = {"target_temperature": _temperature, "ramp": _int(1)}
DISK_CHECKS = {**CPU_CHECKS, "warning_temperature": _temperature,
               "alert_temperature": _temperature}
ZFS_CHECKS = {"poll_interval": _positive, "alert_threshold": _fraction, "bays": _bays}
assert set(FAN_CHECKS) == {f.name for f in fields(Fan)}
assert set(DISK_CHECKS) == {f.name for f in fields(Disk)}
assert set(CPU_CHECKS) == {f.name for f in fields(Cpu)}
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
    _reject_unknown("top level", data, {"vmid", "debug", "fan", "disk", "cpu", "zfs", "power"})
    vmid = str(_checked("vmid", _int(1), data.get("vmid", DEFAULT_VMID)))
    fan = _settings(Fan, "fan", _table(data, "fan"), FAN_CHECKS)
    if fan.min_pwm > fan.max_pwm:
        raise ConfigError("fan.min_pwm must not exceed fan.max_pwm")
    # [disk] holds the disk settings and, as [disk.STATE] tables, the disk LED looks.
    disk_table = _table(data, "disk")
    disk = _settings(Disk, "disk", {key: value for key, value in disk_table.items()
                                    if not isinstance(value, dict)}, DISK_CHECKS)
    if disk.warning_temperature >= disk.alert_temperature:
        raise ConfigError("disk.warning_temperature must be below disk.alert_temperature")
    return Config(
        vmid=vmid,
        debug=_checked("debug", _bool, data.get("debug", False)),
        fan=fan,
        disk=disk,
        cpu=_settings(Cpu, "cpu", _table(data, "cpu"), CPU_CHECKS),
        zfs=_settings(Zfs, "zfs", _table(data, "zfs"), ZFS_CHECKS),
        power_looks=_looks("power", _table(data, "power"), POWER_STATES),
        disk_looks=_looks("disk", {key: value for key, value in disk_table.items()
                                   if isinstance(value, dict)}, DISK_STATES),
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
