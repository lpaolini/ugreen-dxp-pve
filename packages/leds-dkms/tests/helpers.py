import os

from ugreen_leds.config import build

ATTRS = ("trigger", "color", "blink_type", "brightness")


def make_config(led_root="/sys/class/leds"):
    """A small valid Config whose LEDs live under `led_root`."""
    return build({
        "netdev": {"device_name": "eth0", "color": "#0040ff"},
        "shutdown": {"color": "#ffffff"},
        "power": {
            "NORMAL": {"priority": 100, "color": "#004010"},
            "FAULT": {"priority": 10, "color": "#ff0000", "effect": "blink:500:500"},
        },
    }, led_root=led_root)


def led_dir(led_root, name):
    """The class device directory of LED `name`, as the driver names it."""
    return os.path.join(led_root, f"ugreen:white:{name}")


def make_led_dirs(led_root, names, attrs=ATTRS):
    """Create fake sysfs LED directories with empty attribute files."""
    for name in names:
        os.makedirs(led_dir(led_root, name), exist_ok=True)
        for attr in attrs:
            open(os.path.join(led_dir(led_root, name), attr), "w").close()
