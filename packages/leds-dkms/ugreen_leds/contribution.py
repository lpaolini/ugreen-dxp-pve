# SPDX-License-Identifier: MIT
"""LED contributions: the one line a producer writes for an LED.

    priority=35 color=#502800 effect=blink:500:500 state=DEGRADED

`priority` and `color` are required. Across producers, the lowest priority wins.
"""
import re
from dataclasses import dataclass

MAX_CHARS = 256
MAX_PRIORITY = 999
MAX_MS = 60000
KEYS = ("priority", "color", "effect", "state")

# Used with fullmatch(): `$` would also accept a trailing newline.
_DIGITS = re.compile(r"[0-9]+")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_EFFECT = re.compile(r"(blink|breath):([0-9]+):([0-9]+)")
_STATE = re.compile(r"[A-Za-z0-9_-]{1,32}")


@dataclass(frozen=True)
class Look:
    priority: int
    color: str  # "#rrggbb", lower case
    effect: str = "none"  # "none", "blink:ON:OFF" or "breath:ON:OFF" (milliseconds)
    state: str | None = None  # label for status output; never affects rendering


def make_look(priority, color, effect="none", state=None):
    """Validate the fields and return a Look. Raises ValueError naming the bad field."""
    if not (isinstance(priority, int) and not isinstance(priority, bool)
            and 0 <= priority <= MAX_PRIORITY):
        raise ValueError(f"priority must be an integer 0-{MAX_PRIORITY}, got {priority!r}")
    if not (isinstance(color, str) and _COLOR.fullmatch(color)):
        raise ValueError(f"color must be #rrggbb, got {color!r}")
    if not isinstance(effect, str):
        raise ValueError(f"effect must be a string, got {effect!r}")
    if effect != "none":
        match = _EFFECT.fullmatch(effect)
        if not match or max(int(match[2]), int(match[3])) > MAX_MS:
            raise ValueError(f"effect must be none, blink:ON:OFF or breath:ON:OFF "
                             f"(0-{MAX_MS} ms), got {effect!r}")
    if state is not None and not (isinstance(state, str) and _STATE.fullmatch(state)):
        raise ValueError(f"state must be 1-32 letters, digits, '_' or '-', got {state!r}")
    return Look(priority, color.lower(), effect, state)


def parse_look(text):
    """Parse a contribution file's content. Raises ValueError with the reason."""
    if len(text) > MAX_CHARS:
        raise ValueError(f"longer than {MAX_CHARS} characters")
    if len(text.strip().splitlines()) > 1:
        raise ValueError("more than one line")
    fields = {}
    for token in text.split():
        key, sep, value = token.partition("=")
        if not sep:
            raise ValueError(f"expected key=value, got {token!r}")
        if key not in KEYS:
            raise ValueError(f"unknown key {key!r}")
        if key in fields:
            raise ValueError(f"repeated key {key!r}")
        fields[key] = value
    for key in ("priority", "color"):
        if key not in fields:
            raise ValueError(f"missing {key}")
    if not _DIGITS.fullmatch(fields["priority"]):
        raise ValueError(f"priority must be an integer 0-{MAX_PRIORITY}, "
                         f"got {fields['priority']!r}")
    return make_look(int(fields["priority"]), fields["color"],
                     fields.get("effect", "none"), fields.get("state"))


def format_look(look):
    """The one-line form of `look`, without a trailing newline."""
    text = f"priority={look.priority} color={look.color}"
    if look.effect != "none":
        text += f" effect={look.effect}"
    if look.state is not None:
        text += f" state={look.state}"
    return text


def rgb(color):
    """'#rrggbb' -> 'R G B', the LED driver's colour format."""
    return " ".join(str(int(color[i:i + 2], 16)) for i in (1, 3, 5))
