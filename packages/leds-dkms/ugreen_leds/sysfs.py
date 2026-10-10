# SPDX-License-Identifier: MIT
"""Write attributes to one LED class device."""
import os

from .contribution import rgb


def write_attr(path, value):
    """Write `value` + newline to an existing sysfs attribute in one write()."""
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)  # no O_CREAT: missing attributes fail
    try:
        os.write(fd, f"{value}\n".encode())
    finally:
        os.close(fd)


def look_attrs(look):
    """The (attribute, value) writes that show `look`, in order: trigger first.

    Black is an LED switched off: colour 0 0 0 at brightness 1, without effect.
    """
    if look.color == "#000000":
        return (("trigger", "none"), ("color", "0 0 0"), ("blink_type", "none"),
                ("brightness", "1"))
    blink = look.effect.replace(":", " ")  # "blink:500:500" -> "blink 500 500"; "none" stays
    return (("trigger", "none"), ("color", rgb(look.color)), ("blink_type", blink),
            ("brightness", "255"))


def apply(led_path, attrs, write=write_attr):
    """Write every (attribute, value) of `attrs` to `led_path`, in order.

    Returns error messages for attributes that could not be written (empty on success).
    """
    errors = []
    for attr, value in attrs:
        path = os.path.join(led_path, attr)
        try:
            write(path, value)
        except OSError as e:
            errors.append(f"{path}={value!r}: {e.strerror or e}")
    return errors
