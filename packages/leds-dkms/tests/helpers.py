import os

from ugreen_leds.config import build


def make_config(led_root="/sys/class/leds", leds=("power", "disk1")):
    """A small valid Config whose LED paths live under `led_root`."""
    return build({
        "leds": {
            name: {"path": os.path.join(led_root, name),
                   "default": "NORMAL" if name == "power" else "OFF"}
            for name in leds
        },
        "states": {
            "NORMAL": {"priority": 0, "trigger": "none", "color": "0 64 16"},
            "FAULT": {"priority": 100, "trigger": "none", "color": "255 0 0",
                      "blink_type": "blink 500 500"},
            "OFF": {"priority": 0, "color": "0 0 0"},
            "ONLINE": {"priority": 10, "color": "0 40 0"},
            "DEGRADED": {"priority": 40, "color": "80 40 0"},
            "FAULTED": {"priority": 70, "color": "80 0 0"},
        },
    })


def make_led_dirs(led_root, names, attrs=("trigger", "color", "blink_type", "brightness")):
    """Create fake sysfs LED directories with empty attribute files."""
    for name in names:
        os.makedirs(os.path.join(led_root, name), exist_ok=True)
        for attr in attrs:
            open(os.path.join(led_root, name, attr), "w").close()
