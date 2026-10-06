# SPDX-License-Identifier: MIT
"""The producer state tree: /run/ugreen-dxp-pve/<producer>/<led>."""
import os

RUN_ROOT = "/run/ugreen-dxp-pve"
MAX_STATE_BYTES = 256


def producer_dirs(run_root):
    """Sorted paths of the producer directories under `run_root` (dot names skipped)."""
    try:
        with os.scandir(run_root) as entries:
            return sorted(
                entry.path for entry in entries
                if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False)
            )
    except FileNotFoundError:
        return []


def scan(run_root):
    """Return {led: [(producer, raw_content), ...]} sorted by producer.

    Dot files (in-progress atomic writes) and non-regular files are skipped, as
    are files that vanish while scanning.
    """
    result = {}
    for directory in producer_dirs(run_root):
        producer = os.path.basename(directory)
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda entry: entry.name)
        except FileNotFoundError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_file(follow_symlinks=False):
                continue
            try:
                with open(entry.path, encoding="utf-8", errors="replace") as f:
                    raw = f.read(MAX_STATE_BYTES)
            except FileNotFoundError:
                continue
            result.setdefault(entry.name, []).append((producer, raw))
    return result


def producer_dir(run_root=RUN_ROOT, environ=os.environ):
    """Where the caller publishes: its systemd RuntimeDirectory, else <run_root>/manual."""
    return environ.get("RUNTIME_DIRECTORY", "").split(":")[0] or os.path.join(run_root, "manual")


def publish(directory, led, state):
    """Atomically write `state` as the content of `directory/led`."""
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{led}.tmp")
    with open(tmp, "w") as f:
        f.write(f"{state}\n")
    os.replace(tmp, os.path.join(directory, led))


def clear(directory, led):
    """Remove `directory/led` if it exists."""
    try:
        os.unlink(os.path.join(directory, led))
    except FileNotFoundError:
        pass
