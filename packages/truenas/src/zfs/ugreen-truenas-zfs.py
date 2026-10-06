#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Poll ZFS status inside the TrueNAS VM and publish front-panel disk LED states.
# Requires: qemu-guest-agent running inside TrueNAS and ugreen-dxp-pve-leds.service
# on the host, which renders the states published under /run/ugreen-dxp-pve/zfs.
import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import syslog
import threading

sys.dont_write_bytecode = True  # never leave .pyc files in the packaged library
sys.path.append("/usr/lib/ugreen-dxp-pve-leds")  # shipped by ugreen-dxp-pve-leds-dkms
from ugreen_leds.tree import publish, runtime_dir  # noqa: E402

VMID = os.environ.get("VMID", "").strip()
TAG = "ugreen-truenas-zfs"
DEBUG = os.environ.get("DEBUG", "1") == "1"
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "30"))
# Bay N is wired to the path shown — UGREEN backplane constant.
# Verify with `ls -l /dev/disk/by-path/` inside the guest.
BAYS = {
    "1": os.environ.get("BAY_1_PATH", "/dev/disk/by-path/pci-0000:00:10.0-ata-1"),
    "2": os.environ.get("BAY_2_PATH", "/dev/disk/by-path/pci-0000:00:10.0-ata-2"),
    "3": os.environ.get("BAY_3_PATH", "/dev/disk/by-path/pci-0000:00:10.0-ata-3"),
    "4": os.environ.get("BAY_4_PATH", "/dev/disk/by-path/pci-0000:00:10.0-ata-4"),
}

# Severity ordering, so a partition-level FAULTED wins over a disk-level ONLINE.
# ONLINE_ALERT ranks just above ONLINE so a disk that is healthy-but-full on one
# pool beats a plain-ONLINE sibling, but still loses to anything actually broken.
STATE_RANK = {"ONLINE": 0, "ONLINE_ALERT": 1, "OFFLINE": 2, "DEGRADED": 3,
              "REMOVED": 4, "UNAVAIL": 5, "FAULTED": 6}
UNRANKED = len(STATE_RANK)  # rank for any unexpected state — sorts above all known ones

# Fill ratio at or above which an ONLINE leaf is upgraded to ONLINE_ALERT.
ALERT_THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", "0.75"))

syslog.openlog(TAG)
STOP_REQUESTED = threading.Event()


def log(msg):
    syslog.syslog(msg)
    print(f"[{TAG}] {msg}", file=sys.stderr)


def dbg(msg):
    if DEBUG:
        print(f"[{TAG}][debug] {msg}", file=sys.stderr)


def require_vmid():
    if VMID:
        return True
    log("VMID is not configured; set VMID in /etc/ugreen-dxp-pve-truenas-zfs.conf")
    return False


def _guest_spindown_arg(bay, path):
    quoted = shlex.quote(path)
    return (
        f"--arg spindown{bay} \"$("
        "if command -v hdparm >/dev/null 2>&1; then "
        f"hdparm -C {quoted} 2>/dev/null | "
        "awk -F: '/drive state is/ {gsub(/^[ \\t]+|[ \\t]+$/, \"\", $2); print $2}'"
        "; fi"
        ")\""
    )


def set_led(n, state_key):
    dbg(f"LED {n} -> {state_key}")
    directory = runtime_dir()
    if directory is None:  # run by hand, outside the systemd unit: leave no state behind
        return False
    try:
        publish(directory, f"disk{n}", state_key)
    except OSError as e:
        log(f"LED state publish failed: disk{n}={state_key}: {e}")
        return False
    return True


def set_all_leds(state_key):
    ok = True
    for n in BAYS:
        ok = set_led(n, state_key) and ok
    return ok


def build_guest_cmd(bays):
    """Shell command the guest agent runs: ship lsblk, all zpool status, and each bay's
    current disk name and power state back as one JSON object."""
    lines = [
        "jq -n",
        "--argjson lsblk \"$(lsblk -Jlo NAME,PARTUUID,TYPE,PKNAME 2>/dev/null || echo '{}')\"",
        "--argjson zpool \"$(zpool status -pj 2>/dev/null || echo '{}')\"",
    ]
    for bay, path in bays.items():
        lines.append(
            f"--arg bay{bay} \"$(readlink -e {shlex.quote(path)} 2>/dev/null | sed 's|.*/||')\""
        )
        lines.append(_guest_spindown_arg(bay, path))
    bays_obj = ",".join(f'"{bay}":$bay{bay}' for bay in bays)
    spindown_obj = ",".join(f'"{bay}":$spindown{bay}' for bay in bays)
    lines.append(
        "'{lsblk:$lsblk, zpool:$zpool, bays:{"
        + bays_obj
        + "}, spindown:{"
        + spindown_obj
        + "}}'"
    )
    return " ".join(lines)


def flash_checking_pattern():
    """Flash all front-panel LEDs while the guest is queried."""
    set_all_leds("CHECKING")


def fetch_guest_report():
    """Run the guest command via qm; return the parsed guest JSON report.
    On any failure, drive LEDs to an error state and return None."""
    dbg(f"Querying guest for report...")
    try:
        proc = subprocess.run(
            ["qm", "guest", "exec", VMID, "--timeout", "20",
             "--", "/bin/sh", "-c", build_guest_cmd(BAYS)],
            capture_output=True, text=True, check=True,
        )
        payload = json.loads(proc.stdout or "{}")
    except subprocess.CalledProcessError as e:
        log(f"qm guest exec failed (rc={e.returncode}): {(e.stderr or '').strip()}")
        set_all_leds("ERROR")
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        log(f"qm guest exec failed: {e}")
        set_all_leds("ERROR")
        return None

    rc = payload.get("exitcode", 1)
    out = payload.get("out-data", "") or ""
    dbg(f"Queried guest for report status code={rc}, length={len(out)}")
    if rc != 0 or not out:
        log(f"guest command failed (rc={rc})")
        set_all_leds("ERROR")
        return None

    try:
        # return json.loads(out)
        report = json.loads(out)
        bays = report.get("bays", {})
        spindown = report.get("spindown", {})
        lsblk = report.get("lsblk", {})
        zpool = report.get("zpool", {})
        return bays, spindown, lsblk, zpool
    except json.JSONDecodeError as e:
        log(f"guest JSON parse failed: {e}; raw={out[:200]!r}")
        set_all_leds("ERROR")
        return None


def build_device_to_partuuids(lsblk_obj):
    """{disk_name: [partuuid, ...]} — for every disk in lsblk, the partuuids of its partitions.
    Disk name (e.g. "sdb") is what /dev/disk/by-path/ symlinks resolve to; partuuid is what
    ZFS uses as the vdev name."""
    devs = lsblk_obj.get("blockdevices", [])
    disks = {d["name"] for d in devs if d.get("type") == "disk"}
    result = {n: [] for n in disks}
    for d in devs:
        if d.get("type") != "part":
            continue
        parent, partuuid = d.get("pkname"), d.get("partuuid")
        if parent in result and partuuid:
            result[parent].append(partuuid)
    return result


def _iter_leaf_disk_vdevs(vdev, ancestor_fill=None):
    """Yield (leaf, fill_ratio) for each leaf-disk descendant. fill_ratio is
    alloc_space/total_space of the nearest ancestor vdev (or self) that exposes
    both fields, or None if none does. Requires `zpool status -p` for numeric
    values; human-readable values like '7.44T' are silently ignored."""
    fill = ancestor_fill
    alloc, total = vdev.get("alloc_space"), vdev.get("total_space")
    if alloc is not None and total is not None:
        try:
            t = float(total)
            if t > 0:
                fill = float(alloc) / t
        except (TypeError, ValueError):
            pass  # keep inherited fill
    if vdev.get("vdev_type") == "disk":
        yield vdev, fill
    for child in (vdev.get("vdevs") or {}).values():
        yield from _iter_leaf_disk_vdevs(child, fill)


def _pool_leaf_disk_vdevs(pool):
    for vdev in (pool.get("vdevs") or {}).values():
        yield from _iter_leaf_disk_vdevs(vdev)


def _associated_pool_leaves(zpool_obj, associated_partuuids):
    """Yield leaf vdevs for pools that contain a configured-bay disk."""
    wanted = set(associated_partuuids)
    if not wanted:
        return
    for pool in (zpool_obj.get("pools") or {}).values():
        leaves = list(_pool_leaf_disk_vdevs(pool))
        if not any((leaf.get("name") in wanted) for leaf, _fill in leaves):
            continue
        yield pool, leaves


def build_vdev_state(zpool_obj, associated_partuuids):
    """{partuuid: state} for every leaf disk in pools tied to configured bays.
    ONLINE leaves whose nearest enclosing vdev is ≥ ALERT_THRESHOLD full are
    promoted to ONLINE_ALERT."""
    vdev_state = {}
    for _pool, leaves in _associated_pool_leaves(zpool_obj, associated_partuuids):
        for leaf, fill in leaves:
            name, state = leaf.get("name"), leaf.get("state")
            if not (name and state):
                continue
            if state == "ONLINE" and fill is not None and fill >= ALERT_THRESHOLD:
                state = "ONLINE_ALERT"
            prev = vdev_state.get(name)
            if prev is None or STATE_RANK.get(state, UNRANKED) > STATE_RANK.get(prev, 0):
                vdev_state[name] = state
    return vdev_state


def is_resilvering(zpool_obj, associated_partuuids):
    """True if any associated pool has an active RESILVER scan."""
    for pool, _leaves in _associated_pool_leaves(zpool_obj, associated_partuuids):
        scan = pool.get("scan_stats") or {}
        if scan.get("function") == "RESILVER" and scan.get("state") not in ("NONE", "FINISHED", None):
            return True
    return False


def is_spindown_state(power_state):
    normalized = (power_state or "").strip().lower()
    return normalized in ("standby", "sleeping")


def update_leds(bays, spindown, device_to_partuuids, vdev_state, resilvering):
    """Paint each populated bay for its disk's ZFS state.
    Returns (empty_bays, present_partuuids)."""
    present_partuuids = set()
    empty_bays = []
    for bay in BAYS:
        device = bays.get(bay)
        if not device:
            empty_bays.append(bay)
            continue

        partuuids = device_to_partuuids.get(device, [])
        present_partuuids.update(partuuids)
        states = [vdev_state[p] for p in partuuids if p in vdev_state]
        zpool_state = max(states, key=lambda s: STATE_RANK.get(s, UNRANKED)) if states else None

        power_state = spindown.get(bay, "")
        dbg(
            f"Bay {bay}: device={device} partuuids={partuuids} "
            f"state={zpool_state} power={power_state!r}"
        )
        key = zpool_state if zpool_state in STATE_RANK else "OFF"
        if key in ("ONLINE", "ONLINE_ALERT") and resilvering:
            key = "RESILVER"
        elif key == "ONLINE" and is_spindown_state(power_state):
            key = "SPINDOWN"
        set_led(bay, key)

    populated = sum(1 for b in BAYS if bays.get(b))

    """Paint empty bays, one per vdev that ZFS expects but no bay holds.
    Remaining empty bays go OFF; logs a warning if missing > empty."""
    missing_count = sum(1 for v in vdev_state if v not in present_partuuids)

    dbg(
        f"Result: populated bays={populated}/{len(BAYS)}, vdev leaves={len(vdev_state)}, "
        f"empty bays={empty_bays}, missing vdevs={missing_count}, resilvering={int(resilvering)}"
    )

    if missing_count > len(empty_bays):
        log(f"missing vdevs ({missing_count}) exceed empty bays ({len(empty_bays)}); "
            f"some pulls won't be indicated")
    for i, bay in enumerate(empty_bays):
        set_led(bay, "MISSING" if i < missing_count else "OFF")


def control_once():
    dbg(f"Starting: VMID={VMID}")
    flash_checking_pattern()

    report = fetch_guest_report()
    if report is None:
        return False

    bays, spindown, lsblk, zpool = report
    device_to_partuuids = build_device_to_partuuids(lsblk)
    bay_partuuids = {
        partuuid
        for device in bays.values()
        if device
        for partuuid in device_to_partuuids.get(device, [])
    }
    vdev_state = build_vdev_state(zpool, bay_partuuids)
    resilvering = is_resilvering(zpool, bay_partuuids)

    update_leds(bays, spindown, device_to_partuuids, vdev_state, resilvering)
    return True


def request_stop(signum, _frame):
    dbg(f"Received signal {signum}, stopping")
    STOP_REQUESTED.set()


def run_loop():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    log(f"starting ZFS LED control loop, interval={POLL_INTERVAL:g}s")
    while not STOP_REQUESTED.is_set():
        control_once()
        STOP_REQUESTED.wait(POLL_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="Publish UGREEN front-panel LED states from TrueNAS ZFS status")
    parser.add_argument("--start", action="store_true", help="run continuously instead of polling once")
    args = parser.parse_args()

    if not require_vmid():
        sys.exit(1)

    if args.start:
        run_loop()
        return

    sys.exit(0 if control_once() else 1)


if __name__ == "__main__":
    main()
