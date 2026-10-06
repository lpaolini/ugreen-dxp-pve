# SPDX-License-Identifier: MIT
"""Write a state's attributes to one LED class device."""
import os


class LedMissing(Exception):
    pass


def write_attr(path, value):
    """Write `value` + newline to an existing sysfs attribute in one write()."""
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)  # no O_CREAT: missing attributes fail
    try:
        os.write(fd, f"{value}\n".encode())
    finally:
        os.close(fd)


def apply(led_path, state, write=write_attr):
    """Write every attribute of `state` to `led_path`, in order.

    Raises LedMissing if the LED directory does not exist. Returns a list of
    error messages for attributes that could not be written (empty on success).
    """
    if not os.path.isdir(led_path):
        raise LedMissing(led_path)
    errors = []
    for attr, value in state.attrs:
        path = os.path.join(led_path, attr)
        try:
            write(path, value)
        except OSError as e:
            errors.append(f"{path}={value!r}: {e.strerror or e}")
    return errors
