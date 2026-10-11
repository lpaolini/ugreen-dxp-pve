#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Poll disk temperatures inside the TrueNAS VM and drive the UGREEN rear fan.
# Requires: qemu-guest-agent running inside TrueNAS, lm-sensors/drivetemp in the
# guest, and the UGREEN/it87 hwmon driver exposed on the Proxmox host.
import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import syslog
import threading
from dataclasses import dataclass

sys.dont_write_bytecode = True  # never leave .pyc files in the packaged library
# The LED library is shipped by ugreen-dxp-pve-leds-dkms.
sys.path[:0] = ["/usr/lib/ugreen-dxp-pve-truenas", "/usr/lib/ugreen-dxp-pve-leds"]
from ugreen_leds.tree import clear, publish, runtime_dir, runtime_dirs  # noqa: E402
from ugreen_truenas.config import CONFIG_PATH, ConfigError, Fan, load  # noqa: E402

TAG = "ugreen-truenas-fan"
HWMON_ROOT = "/sys/class/hwmon"
HWMON_NAME_REGEX = r"^it[0-9]+$"  # the ITE chip driven by it87, e.g. it8613
MANUAL_PWM_ENABLE_VALUE = "1"  # pwmN_enable values of the hwmon interface
AUTO_PWM_ENABLE_VALUE = "2"
# The sensors -j chips inside TrueNAS that report disk temperatures.
TEMP_CHIP_REGEX = r"(?i)(drivetemp|nvme|ata|scsi|sas|sat|disk|hdd|ssd)"
# Settings, set from the configuration by configure().
VMID = ""
DEBUG = False
FAN_PWM_CHANNEL = ""
DISK_TARGET = CPU_TARGET = 0  # °C: the fan stays at MIN_PWM up to these
DISK_RAMP = CPU_RAMP = 0  # PWM per °C above the target
MIN_PWM = MAX_PWM = FAILSAFE_PWM = 255
POLL_INTERVAL = 30
# Found by resolve_host_hwmon_paths().
FAN_PWM_PATH = FAN_PWM_ENABLE_PATH = FAN_INPUT_PATH = CPU_TEMP_PATH = ""
POWER_LED_FAULT_LOOK = None  # published for the power LED while the fan cannot be controlled
TEMP_WARNING = TEMP_ALERT = 0  # °C, hottest disk
POWER_LED_TEMP_LOOKS = {}  # "TEMP_WARNING" / "TEMP_ALERT" -> Look
TEMP_PRODUCER = "truenas-temp"  # RuntimeDirectory that carries the temperature look

syslog.openlog(TAG)
STOP_REQUESTED = threading.Event()
POWER_LED_FAULT_ACTIVE = None
POWER_LED_TEMP_LEVEL = None  # temperature look published now


@dataclass(frozen=True)
class TempReading:
    chip: str
    feature: str
    input_name: str
    temp_c: float


def log(msg):
    syslog.syslog(msg)
    print(f"[{TAG}] {msg}", file=sys.stderr)


def dbg(msg):
    if DEBUG:
        print(f"[{TAG}][debug] {msg}", file=sys.stderr)


def configure(config):
    """Apply a loaded configuration to the module settings."""
    global VMID, DEBUG, POWER_LED_FAULT_LOOK, POWER_LED_TEMP_LOOKS
    VMID = config.vmid
    DEBUG = config.debug
    POWER_LED_FAULT_LOOK = config.power["FAULT"]
    POWER_LED_TEMP_LOOKS = {name: config.power[name] for name in ("TEMP_WARNING", "TEMP_ALERT")}
    configure_fan(config.fan)


def configure_fan(fan):
    """Apply the [fan] settings to the module settings."""
    global FAN_PWM_CHANNEL, DISK_TARGET, DISK_RAMP, CPU_TARGET, CPU_RAMP
    global MIN_PWM, MAX_PWM, FAILSAFE_PWM, POLL_INTERVAL, TEMP_WARNING, TEMP_ALERT
    global FAN_PWM_PATH, FAN_PWM_ENABLE_PATH, FAN_INPUT_PATH, CPU_TEMP_PATH
    FAN_PWM_CHANNEL = str(fan.pwm_channel)
    DISK_TARGET, DISK_RAMP = fan.disk_target_temperature, fan.disk_ramp
    CPU_TARGET, CPU_RAMP = fan.cpu_target_temperature, fan.cpu_ramp
    MIN_PWM = fan.min_pwm
    MAX_PWM = fan.max_pwm
    FAILSAFE_PWM = fan.failsafe_pwm
    POLL_INTERVAL = fan.poll_interval
    TEMP_WARNING = fan.temp_warning
    TEMP_ALERT = fan.temp_alert
    FAN_PWM_PATH = FAN_PWM_ENABLE_PATH = FAN_INPUT_PATH = CPU_TEMP_PATH = ""


def clamp(value, low, high):
    return max(low, min(high, value))


def _write(path, value, hint=None):
    try:
        with open(path, "w") as f:
            f.write(str(value) + "\n")
    except PermissionError as e:
        suffix = f"; {hint}" if hint else ""
        log(f"sysfs write failed: {path}={value!r}: {e}{suffix}")
        return False
    except OSError as e:
        log(f"sysfs write failed: {path}={value!r}: {e}")
        return False
    return True


def set_power_led_fault(active):
    global POWER_LED_FAULT_ACTIVE

    if POWER_LED_FAULT_ACTIVE is active:
        return True
    directory = runtime_dir()
    if directory is None:  # run by hand, outside the systemd unit: leave no state behind
        return False
    try:
        if active:
            publish(directory, "power", POWER_LED_FAULT_LOOK)
        else:
            clear(directory, "power")
    except OSError as e:
        log(f"power LED state update failed: {e}")
        return False
    POWER_LED_FAULT_ACTIVE = active
    return True


def temperature_level(temp_c):
    """The power LED look for the hottest disk temperature, or None below the warning."""
    if temp_c >= TEMP_ALERT:
        return "TEMP_ALERT"
    if temp_c >= TEMP_WARNING:
        return "TEMP_WARNING"
    return None


def temperature_dir():
    """This unit's RuntimeDirectory for the temperature look, or None outside systemd."""
    return next((path for path in runtime_dirs() if os.path.basename(path) == TEMP_PRODUCER),
                None)


def set_power_led_temperature(level):
    """Publish the power LED look named `level`, or withdraw it for None."""
    global POWER_LED_TEMP_LEVEL

    if level == POWER_LED_TEMP_LEVEL:
        return True
    directory = temperature_dir()
    if directory is None:  # run by hand, outside the systemd unit: leave no state behind
        return False
    try:
        if level is None:
            clear(directory, "power")
        else:
            publish(directory, "power", POWER_LED_TEMP_LOOKS[level])
    except OSError as e:
        log(f"power LED temperature update failed: {e}")
        return False
    POWER_LED_TEMP_LEVEL = level
    return True


def apply_fan_pwm(pwm):
    ok = set_fan_pwm(pwm)
    set_power_led_fault(not ok)
    return ok


def apply_fan_auto():
    ok = set_fan_auto()
    set_power_led_fault(not ok)
    return ok


def set_fan_pwm(pwm):
    pwm = int(clamp(pwm, 0, 255))
    dbg(f"Fan PWM -> {pwm}")

    if not FAN_PWM_PATH:
        log("no fan PWM path: the it87 hwmon device was not found")
        return False
    if not os.path.exists(FAN_PWM_PATH):
        log(f"PWM path missing: {FAN_PWM_PATH}")
        return False

    ok = True
    hint = "check [fan] pwm_channel"

    if FAN_PWM_ENABLE_PATH and os.path.exists(FAN_PWM_ENABLE_PATH):
        ok = _write(FAN_PWM_ENABLE_PATH, MANUAL_PWM_ENABLE_VALUE, hint) and ok
    elif FAN_PWM_ENABLE_PATH:
        log(f"PWM enable path missing: {FAN_PWM_ENABLE_PATH}")
        ok = False
    else:
        dbg("[fan] pwm_enable_path is empty, skipping manual-mode write")

    ok = _write(FAN_PWM_PATH, pwm, hint) and ok
    rpm = read_int(FAN_INPUT_PATH)
    rpm_text = "unknown" if rpm is None else str(rpm)
    log(f"fan pwm={pwm}, rpm={rpm_text}, path={FAN_PWM_PATH}")
    return ok


def set_fan_auto():
    if not FAN_PWM_ENABLE_PATH:
        log("no fan PWM enable path: the it87 hwmon device was not found")
        return False
    if not os.path.exists(FAN_PWM_ENABLE_PATH):
        log(f"PWM enable path missing: {FAN_PWM_ENABLE_PATH}")
        return False

    hint = "check [fan] pwm_channel"
    ok = _write(FAN_PWM_ENABLE_PATH, AUTO_PWM_ENABLE_VALUE, hint)
    if ok:
        log(f"fan pwm control reset to auto: {FAN_PWM_ENABLE_PATH}={AUTO_PWM_ENABLE_VALUE}")
    return ok


def read_int(path):
    if not path:
        return None
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def read_temp_c(path):
    raw = read_int(path)
    if raw is None:
        return None
    return normalize_temp(raw)


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def hwmon_name(hwmon_dir):
    if not hwmon_dir:
        return None
    return read_text(os.path.join(hwmon_dir, "name"))


def find_hwmon():
    """The hwmon directory of the it87 chip, or None."""
    for hwmon_dir in sorted(glob.glob(os.path.join(HWMON_ROOT, "hwmon*"))):
        name = hwmon_name(hwmon_dir)
        if name and re.search(HWMON_NAME_REGEX, name):
            return hwmon_dir
    return None


def resolve_host_hwmon_paths():
    global FAN_PWM_PATH, FAN_PWM_ENABLE_PATH, FAN_INPUT_PATH, CPU_TEMP_PATH

    hwmon_dir = find_hwmon()
    if not hwmon_dir:
        log(f"no hwmon device named like {HWMON_NAME_REGEX!r} in {HWMON_ROOT}: "
            "is the it87 driver loaded?")
        return

    channel = FAN_PWM_CHANNEL
    FAN_PWM_PATH = os.path.join(hwmon_dir, f"pwm{channel}")
    FAN_PWM_ENABLE_PATH = os.path.join(hwmon_dir, f"pwm{channel}_enable")
    FAN_INPUT_PATH = os.path.join(hwmon_dir, f"fan{channel}_input")
    CPU_TEMP_PATH = os.path.join(hwmon_dir, "temp1_input")
    dbg(
        "Host hwmon paths resolved: "
        f"hwmon={hwmon_dir}, name={hwmon_name(hwmon_dir)}, channel={channel}, pwm={FAN_PWM_PATH}, "
        f"pwm_enable={FAN_PWM_ENABLE_PATH}, fan_input={FAN_INPUT_PATH}, "
        f"cpu_temp={CPU_TEMP_PATH}"
    )


def pwm_for_temp(temp_c, target, ramp):
    """MIN_PWM up to `target` °C, then `ramp` more PWM per °C, up to MAX_PWM."""
    return int(clamp(round(MIN_PWM + ramp * (temp_c - target)), MIN_PWM, MAX_PWM))


def fetch_guest_sensors():
    dbg(f"Querying guest sensors: VMID={VMID}")
    try:
        proc = subprocess.run(
            ["qm", "guest", "exec", VMID, "--timeout", "20",
             "--", "/bin/sh", "-c", "sensors -j"],
            capture_output=True, text=True, check=True,
        )
        payload = json.loads(proc.stdout or "{}")
    except subprocess.CalledProcessError as e:
        log(f"qm guest exec failed (rc={e.returncode}): {(e.stderr or '').strip()}")
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        log(f"qm guest exec failed: {e}")
        return None

    rc = payload.get("exitcode", 1)
    out = payload.get("out-data", "") or ""
    dbg(f"Queried sensors status code={rc}, length={len(out)}")
    if rc != 0 or not out:
        log(f"guest sensors command failed (rc={rc})")
        return None

    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        log(f"guest sensors JSON parse failed: {e}; raw={out[:200]!r}")
        return None


def normalize_temp(value):
    try:
        temp = float(value)
    except (TypeError, ValueError):
        return None
    # sysfs-style millidegrees sometimes leak through wrappers; sensors -j
    # normally returns plain Celsius.
    if temp > 1000:
        temp /= 1000
    if temp < -40 or temp > 125:
        return None
    return temp


def collect_disk_temps(sensors_obj):
    chip_re = re.compile(TEMP_CHIP_REGEX)
    readings = []
    for chip, chip_data in (sensors_obj or {}).items():
        if not isinstance(chip_data, dict) or not chip_re.search(chip):
            continue
        for feature, feature_data in chip_data.items():
            if not isinstance(feature_data, dict):
                continue
            for input_name, value in feature_data.items():
                if not input_name.endswith("_input"):
                    continue
                temp = normalize_temp(value)
                if temp is not None:
                    readings.append(TempReading(chip, feature, input_name, temp))
    return readings


def describe_readings(readings):
    return ", ".join(
        f"{r.chip}/{r.feature}/{r.input_name}={r.temp_c:.1f}C"
        for r in sorted(readings, key=lambda r: r.temp_c, reverse=True)
    )


def control_once():
    cpu_temp = read_temp_c(CPU_TEMP_PATH)
    if cpu_temp is None:
        set_power_led_temperature(None)
        log(f"CPU temperature read failed: {CPU_TEMP_PATH}")
        apply_fan_pwm(FAILSAFE_PWM)
        return False

    sensors_obj = fetch_guest_sensors()
    if sensors_obj is None:
        set_power_led_temperature(None)
        apply_fan_pwm(FAILSAFE_PWM)
        return False

    readings = collect_disk_temps(sensors_obj)
    if not readings:
        set_power_led_temperature(None)
        log(f"no disk temperature readings matched temp_chip_regex={TEMP_CHIP_REGEX!r}")
        apply_fan_pwm(FAILSAFE_PWM)
        return False

    hottest = max(readings, key=lambda r: r.temp_c)
    set_power_led_temperature(temperature_level(hottest.temp_c))
    hdd_pwm = pwm_for_temp(hottest.temp_c, DISK_TARGET, DISK_RAMP)
    cpu_pwm = pwm_for_temp(cpu_temp, CPU_TARGET, CPU_RAMP)
    pwm = max(hdd_pwm, cpu_pwm)
    dbg(f"Disk temperatures: {describe_readings(readings)}")
    log(
        f"hottest disk temp={hottest.temp_c:.1f}C ({hottest.chip}/{hottest.feature}), "
        f"hdd_pwm={hdd_pwm}, cpu temp={cpu_temp:.1f}C, cpu_pwm={cpu_pwm}, pwm={pwm}"
    )
    return apply_fan_pwm(pwm)


def request_stop(signum, _frame):
    dbg(f"Received signal {signum}, stopping")
    STOP_REQUESTED.set()


def run_loop():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log(f"starting fan control loop, interval={POLL_INTERVAL:g}s")
    try:
        while not STOP_REQUESTED.is_set():
            control_once()
            STOP_REQUESTED.wait(POLL_INTERVAL)
    finally:
        apply_fan_auto()


def main():
    parser = argparse.ArgumentParser(description="Drive UGREEN fan PWM from TrueNAS disk and host CPU temperatures")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--start", action="store_true", help="run continuously instead of polling once")
    mode.add_argument("--stop", action="store_true", help="reset the fan PWM controller to automatic mode and exit")
    parser.add_argument("--config", default=CONFIG_PATH, help="configuration file (default: %(default)s)")
    args = parser.parse_args()

    try:
        configure(load(args.config))
    except ConfigError as e:
        log(f"invalid configuration: {e}")
        if not args.stop:
            sys.exit(1)
        # Still hand the fan back to automatic control, using the built-in defaults.
        configure_fan(Fan())
        resolve_host_hwmon_paths()
        sys.exit(0 if set_fan_auto() else 1)

    resolve_host_hwmon_paths()

    if args.stop:
        sys.exit(0 if apply_fan_auto() else 1)

    if args.start:
        run_loop()
        return

    sys.exit(0 if control_once() else 1)


if __name__ == "__main__":
    main()
