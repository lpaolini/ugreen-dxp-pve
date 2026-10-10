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
from ugreen_leds.tree import clear, publish, runtime_dir  # noqa: E402
from ugreen_truenas.config import CONFIG_PATH, ConfigError, Fan, load  # noqa: E402

TAG = "ugreen-truenas-fan"
# Settings, set from the configuration by configure().
VMID = ""
DEBUG = False
FAN_HWMON_NAME = FAN_HWMON_REGEX = FAN_PWM_CHANNEL = ""
AUTO_DISCOVER_HWMON = False
FAN_PWM_PATH = FAN_PWM_ENABLE_PATH = FAN_INPUT_PATH = CPU_TEMP_PATH = ""
HDD_CURVE = CPU_CURVE = ()  # ((temperature, pwm), ...) sorted by temperature
MIN_PWM = MAX_PWM = FAILSAFE_PWM = 255
MANUAL_PWM_ENABLE_VALUE = AUTO_PWM_ENABLE_VALUE = ""
POLL_INTERVAL = 30
RESET_PWM_ON_EXIT = True
TEMP_CHIP_REGEX = ""
POWER_LED_FAULT_LOOK = None  # published for the power LED while the fan cannot be controlled

syslog.openlog(TAG)
STOP_REQUESTED = threading.Event()
POWER_LED_FAULT_ACTIVE = None


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
    global VMID, DEBUG, POWER_LED_FAULT_LOOK
    VMID = config.vmid or ""
    DEBUG = config.debug
    POWER_LED_FAULT_LOOK = config.power["FAULT"]
    configure_fan(config.fan)


def configure_fan(fan):
    """Apply the [fan] settings to the module settings."""
    global FAN_HWMON_NAME, FAN_HWMON_REGEX, FAN_PWM_CHANNEL, AUTO_DISCOVER_HWMON
    global FAN_PWM_PATH, FAN_PWM_ENABLE_PATH, FAN_INPUT_PATH, CPU_TEMP_PATH
    global HDD_CURVE, CPU_CURVE, MIN_PWM, MAX_PWM, FAILSAFE_PWM
    global MANUAL_PWM_ENABLE_VALUE, AUTO_PWM_ENABLE_VALUE, POLL_INTERVAL, RESET_PWM_ON_EXIT
    global TEMP_CHIP_REGEX
    FAN_HWMON_NAME = fan.hwmon_name
    FAN_HWMON_REGEX = fan.hwmon_regex
    FAN_PWM_CHANNEL = str(fan.pwm_channel)
    AUTO_DISCOVER_HWMON = fan.auto_discover_hwmon
    FAN_PWM_PATH = fan.pwm_path
    FAN_PWM_ENABLE_PATH = fan.pwm_enable_path
    FAN_INPUT_PATH = fan.input_path
    CPU_TEMP_PATH = fan.cpu_temp_path
    HDD_CURVE = fan.hdd_curve
    CPU_CURVE = fan.cpu_curve
    MIN_PWM = fan.min_pwm
    MAX_PWM = fan.max_pwm
    FAILSAFE_PWM = fan.failsafe_pwm
    MANUAL_PWM_ENABLE_VALUE = str(fan.manual_pwm_enable_value)
    AUTO_PWM_ENABLE_VALUE = str(fan.auto_pwm_enable_value)
    POLL_INTERVAL = fan.poll_interval
    RESET_PWM_ON_EXIT = fan.reset_pwm_on_exit
    TEMP_CHIP_REGEX = fan.temp_chip_regex


def require_vmid():
    if VMID:
        return True
    log("vmid is not configured; set vmid in /etc/ugreen-dxp-pve-truenas.toml")
    return False


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
        log("[fan] pwm_path is not set and hwmon auto-discovery did not resolve it")
        return False
    if not os.path.exists(FAN_PWM_PATH):
        log(f"PWM path missing: {FAN_PWM_PATH}")
        return False

    ok = True
    hint = "check [fan] hwmon_name/pwm_channel or set pwm_path"

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
        log("[fan] pwm_enable_path is not set and hwmon auto-discovery did not resolve it")
        return False
    if not os.path.exists(FAN_PWM_ENABLE_PATH):
        log(f"PWM enable path missing: {FAN_PWM_ENABLE_PATH}")
        return False

    hint = "check [fan] hwmon_name/pwm_channel or set pwm_enable_path"
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


def hwmon_dir_for(path):
    if not path:
        return None

    directory = os.path.dirname(path)
    if os.path.basename(directory).startswith("hwmon"):
        return directory
    return None


def hwmon_name(hwmon_dir):
    if not hwmon_dir:
        return None
    return read_text(os.path.join(hwmon_dir, "name"))


def hwmon_name_matches(name):
    if not name:
        return False

    if FAN_HWMON_NAME and name == FAN_HWMON_NAME:
        return True

    if FAN_HWMON_REGEX:
        return re.search(FAN_HWMON_REGEX, name) is not None

    return False


def find_hwmon():
    if not FAN_HWMON_NAME and not FAN_HWMON_REGEX:
        return None

    for hwmon_dir in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if hwmon_name_matches(hwmon_name(hwmon_dir)):
            return hwmon_dir
    return None


def configured_fan_paths_are_usable():
    if not FAN_PWM_PATH or not FAN_INPUT_PATH:
        return False

    if not os.path.exists(FAN_PWM_PATH) or not os.path.exists(FAN_INPUT_PATH):
        return False

    if FAN_PWM_ENABLE_PATH and not os.path.exists(FAN_PWM_ENABLE_PATH):
        return False

    if FAN_HWMON_NAME or FAN_HWMON_REGEX:
        configured_hwmon = hwmon_dir_for(FAN_PWM_PATH)
        if not hwmon_name_matches(hwmon_name(configured_hwmon)):
            return False

    return True


def resolve_host_hwmon_paths():
    global FAN_PWM_PATH, FAN_PWM_ENABLE_PATH, FAN_INPUT_PATH, CPU_TEMP_PATH

    if not AUTO_DISCOVER_HWMON or configured_fan_paths_are_usable():
        return

    hwmon_dir = find_hwmon()
    if not hwmon_dir:
        dbg(
            "Could not find matching fan hwmon device "
            f"([fan] hwmon_name={FAN_HWMON_NAME!r}, hwmon_regex={FAN_HWMON_REGEX!r}); "
            "using configured fan paths"
        )
        return

    channel = FAN_PWM_CHANNEL
    pwm_path = os.path.join(hwmon_dir, f"pwm{channel}")
    pwm_enable_path = os.path.join(hwmon_dir, f"pwm{channel}_enable")
    fan_input_path = os.path.join(hwmon_dir, f"fan{channel}_input")
    cpu_temp_path = os.path.join(hwmon_dir, "temp1_input")

    FAN_PWM_PATH = pwm_path
    FAN_PWM_ENABLE_PATH = pwm_enable_path
    FAN_INPUT_PATH = fan_input_path

    if not CPU_TEMP_PATH or not os.path.exists(CPU_TEMP_PATH):
        CPU_TEMP_PATH = cpu_temp_path

    dbg(
        "Host hwmon paths resolved: "
        f"hwmon={hwmon_dir}, name={hwmon_name(hwmon_dir)}, channel={channel}, pwm={FAN_PWM_PATH}, "
        f"pwm_enable={FAN_PWM_ENABLE_PATH}, fan_input={FAN_INPUT_PATH}, "
        f"cpu_temp={CPU_TEMP_PATH}"
    )


def pwm_for_temp(temp_c, curve):
    if temp_c <= curve[0][0]:
        return int(clamp(curve[0][1], MIN_PWM, MAX_PWM))
    if temp_c >= curve[-1][0]:
        return int(clamp(curve[-1][1], MIN_PWM, MAX_PWM))

    for (t0, p0), (t1, p1) in zip(curve, curve[1:]):
        if t0 <= temp_c <= t1:
            fraction = (temp_c - t0) / (t1 - t0)
            pwm = round(p0 + (p1 - p0) * fraction)
            return int(clamp(pwm, MIN_PWM, MAX_PWM))

    return FAILSAFE_PWM


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
        log(f"CPU temperature read failed: {CPU_TEMP_PATH}")
        apply_fan_pwm(FAILSAFE_PWM)
        return False

    sensors_obj = fetch_guest_sensors()
    if sensors_obj is None:
        apply_fan_pwm(FAILSAFE_PWM)
        return False

    readings = collect_disk_temps(sensors_obj)
    if not readings:
        log(f"no disk temperature readings matched temp_chip_regex={TEMP_CHIP_REGEX!r}")
        apply_fan_pwm(FAILSAFE_PWM)
        return False

    hottest = max(readings, key=lambda r: r.temp_c)
    hdd_pwm = pwm_for_temp(hottest.temp_c, HDD_CURVE)
    cpu_pwm = pwm_for_temp(cpu_temp, CPU_CURVE)
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
        if RESET_PWM_ON_EXIT:
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

    if not require_vmid():
        sys.exit(1)

    if args.start:
        run_loop()
        return

    sys.exit(0 if control_once() else 1)


if __name__ == "__main__":
    main()
