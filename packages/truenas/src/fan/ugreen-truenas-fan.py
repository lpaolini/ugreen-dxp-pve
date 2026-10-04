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

VMID = os.environ.get("VMID", "").strip()
TAG = "ugreen-truenas-fan"
DEBUG = os.environ.get("DEBUG", "1") == "1"

# DXP4800-class systems expose the wired fan channels as pwm2/pwm3 through it87.
# Keep the path configurable because hwmon numbering can change after boot.
FAN_HWMON_NAME = os.environ.get("FAN_HWMON_NAME", "").strip()
FAN_HWMON_REGEX = os.environ.get("FAN_HWMON_REGEX", r"^it[0-9]+$").strip()
FAN_PWM_CHANNEL = os.environ.get("FAN_PWM_CHANNEL", "3").strip()
AUTO_DISCOVER_HWMON = os.environ.get("AUTO_DISCOVER_HWMON", "1") == "1"
FAN_PWM_PATH = os.environ.get("FAN_PWM_PATH", "").strip()
FAN_PWM_ENABLE_PATH = os.environ.get("FAN_PWM_ENABLE_PATH", "").strip()
FAN_INPUT_PATH = os.environ.get("FAN_INPUT_PATH", "").strip()
CPU_TEMP_PATH = os.environ.get("CPU_TEMP_PATH", "").strip()

# Comma-separated temp:pwm points. Temperatures are Celsius; PWM is 0..255.
# The HDD curve is intentionally conservative and fails to MAX_PWM.
HDD_FAN_CURVE = os.environ.get("HDD_FAN_CURVE", "30:90,35:120,40:175,43:220,45:255")
CPU_FAN_CURVE = os.environ.get("CPU_FAN_CURVE", "45:90,55:115,65:150,75:190,85:225,95:255")
MIN_PWM = int(os.environ.get("MIN_PWM", "90"))
MAX_PWM = int(os.environ.get("MAX_PWM", "255"))
FAILSAFE_PWM = int(os.environ.get("FAILSAFE_PWM", str(MAX_PWM)))
MANUAL_PWM_ENABLE_VALUE = os.environ.get("MANUAL_PWM_ENABLE_VALUE", "1")
AUTO_PWM_ENABLE_VALUE = os.environ.get("AUTO_PWM_ENABLE_VALUE", "2")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "30"))
RESET_PWM_ON_EXIT = os.environ.get("RESET_PWM_ON_EXIT", "1") == "1"
POWER_LED_HELPER = os.environ.get(
    "POWER_LED_HELPER",
    "/usr/libexec/ugreen-dxp-pve-leds-dkms/power-led-ugreen",
).strip()
POWER_LED_FAULT_NAME = os.environ.get("POWER_LED_FAULT_NAME", "fan_control").strip()

# Limit temperature extraction to disk-like sensors so CPU/package temps do not
# spin the storage fan. Override if your TrueNAS sensor chip names differ.
TEMP_CHIP_REGEX = os.environ.get("TEMP_CHIP_REGEX", r"(?i)(drivetemp|nvme|ata|scsi|sas|sat|disk|hdd|ssd)")

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


def require_vmid():
    if VMID:
        return True
    log("VMID is not configured; set VMID in /etc/ugreen-dxp-pve-truenas-fan.conf")
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
    if not POWER_LED_HELPER:
        return False
    if not POWER_LED_FAULT_NAME:
        log("POWER_LED_FAULT_NAME is empty; cannot update power LED fault")
        return False

    action = "set-fault" if active else "clear-fault"
    try:
        proc = subprocess.run(
            [POWER_LED_HELPER, action, POWER_LED_FAULT_NAME],
            capture_output=True, text=True, check=False,
        )
    except OSError as e:
        log(f"power LED helper failed: {e}")
        return False

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if detail:
            log(f"power LED helper {action} failed: {detail}")
        else:
            log(f"power LED helper {action} failed with rc={proc.returncode}")
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
        log("FAN_PWM_PATH is not configured and hwmon auto-discovery did not resolve it")
        return False
    if not os.path.exists(FAN_PWM_PATH):
        log(f"PWM path missing: {FAN_PWM_PATH}")
        return False

    ok = True
    hint = "check FAN_HWMON_NAME/FAN_PWM_CHANNEL or set FAN_PWM_PATH manually"

    if FAN_PWM_ENABLE_PATH and os.path.exists(FAN_PWM_ENABLE_PATH):
        ok = _write(FAN_PWM_ENABLE_PATH, MANUAL_PWM_ENABLE_VALUE, hint) and ok
    elif FAN_PWM_ENABLE_PATH:
        log(f"PWM enable path missing: {FAN_PWM_ENABLE_PATH}")
        ok = False
    else:
        dbg("FAN_PWM_ENABLE_PATH is empty, skipping manual-mode write")

    ok = _write(FAN_PWM_PATH, pwm, hint) and ok
    rpm = read_int(FAN_INPUT_PATH)
    rpm_text = "unknown" if rpm is None else str(rpm)
    log(f"fan pwm={pwm}, rpm={rpm_text}, path={FAN_PWM_PATH}")
    return ok


def set_fan_auto():
    if not FAN_PWM_ENABLE_PATH:
        log("FAN_PWM_ENABLE_PATH is not configured and hwmon auto-discovery did not resolve it")
        return False
    if not os.path.exists(FAN_PWM_ENABLE_PATH):
        log(f"PWM enable path missing: {FAN_PWM_ENABLE_PATH}")
        return False

    hint = "check FAN_HWMON_NAME/FAN_PWM_CHANNEL or set FAN_PWM_ENABLE_PATH manually"
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
        try:
            return re.search(FAN_HWMON_REGEX, name) is not None
        except re.error as e:
            log(f"invalid FAN_HWMON_REGEX={FAN_HWMON_REGEX!r}: {e}")
            return False

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
            f"(FAN_HWMON_NAME={FAN_HWMON_NAME!r}, FAN_HWMON_REGEX={FAN_HWMON_REGEX!r}); "
            "using configured fan paths"
        )
        return

    channel = FAN_PWM_CHANNEL
    if not channel.isdigit():
        log(f"invalid FAN_PWM_CHANNEL={channel!r}; expected a numeric pwm/fan channel")
        return

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


def parse_curve(name, spec):
    points = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            temp, pwm = item.split(":", 1)
            points.append((float(temp), int(pwm)))
        except ValueError as e:
            raise ValueError(f"invalid {name} point {item!r}; expected temp:pwm") from e

    if len(points) < 2:
        raise ValueError(f"{name} must contain at least two temp:pwm points")

    points.sort(key=lambda p: p[0])
    return [(t, int(clamp(p, 0, 255))) for t, p in points]


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
    try:
        hdd_curve = parse_curve("HDD_FAN_CURVE", HDD_FAN_CURVE)
        cpu_curve = parse_curve("CPU_FAN_CURVE", CPU_FAN_CURVE)
    except ValueError as e:
        log(str(e))
        apply_fan_pwm(FAILSAFE_PWM)
        return False

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
        log(f"no disk temperature readings matched TEMP_CHIP_REGEX={TEMP_CHIP_REGEX!r}")
        apply_fan_pwm(FAILSAFE_PWM)
        return False

    hottest = max(readings, key=lambda r: r.temp_c)
    hdd_pwm = pwm_for_temp(hottest.temp_c, hdd_curve)
    cpu_pwm = pwm_for_temp(cpu_temp, cpu_curve)
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
    args = parser.parse_args()

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
