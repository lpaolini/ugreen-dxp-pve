# SPDX-License-Identifier: MIT
"""Colour parsing for the driver's `color` attribute."""
import re

_HEX = re.compile(r"^(?:#|0[xX])?([0-9A-Fa-f]{6})$")
_RGB = re.compile(r"^(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})$")


def normalize_color(value):
    """Return "R G B" for "R G B", "#rrggbb", "0xrrggbb" or "rrggbb".

    Raises ValueError for anything else.
    """
    text = str(value).strip()
    match = _HEX.match(text)
    if match:
        digits = match.group(1)
        return " ".join(str(int(digits[i:i + 2], 16)) for i in (0, 2, 4))
    match = _RGB.match(text)
    if match:
        parts = [int(part) for part in match.groups()]
        if all(part <= 255 for part in parts):
            return " ".join(str(part) for part in parts)
    raise ValueError(f"invalid color {value!r}: expected 'R G B' (0-255) or '#rrggbb'")
