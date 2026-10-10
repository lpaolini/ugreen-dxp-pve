# SPDX-License-Identifier: MIT
"""Pick one look per LED from what producers published."""
from dataclasses import dataclass

from .config import CONTRIBUTED
from .contribution import Look, parse_look

OFF = Look(priority=999, color="#000000", state="OFF")  # a disk LED nobody contributes to


@dataclass(frozen=True)
class Resolution:
    looks: dict  # led -> Look to show, for the power LED and every disk LED
    sources: dict  # led -> winning producer, or "default"
    contributors: dict  # led -> [(producer, Look)] that passed validation
    problems: list  # human-readable messages about ignored files


def resolve(scanned, config):
    """Apply priorities to `scanned` ({led: [(producer, raw)]}).

    The valid look with the lowest priority wins; ties go to the producer whose
    name sorts first. Without a valid look the power LED shows [power.NORMAL]
    and a disk LED is off.
    """
    looks, sources, contributors, problems = {}, {}, {}, []

    for led_name, entries in scanned.items():
        if led_name in CONTRIBUTED:
            continue
        reason = ("the network LED is driven by the daemon" if led_name == "netdev"
                  else "unknown LED")
        problems += [f"{producer}/{led_name}: {reason}" for producer, _ in entries]

    for led_name in CONTRIBUTED:
        valid = []
        for producer, raw in scanned.get(led_name, []):
            try:
                valid.append((producer, parse_look(raw)))
            except ValueError as e:
                problems.append(f"{producer}/{led_name}: {e}")
        contributors[led_name] = valid
        if valid:
            sources[led_name], looks[led_name] = min(
                valid, key=lambda item: (item[1].priority, item[0]))
        else:
            sources[led_name] = "default"
            looks[led_name] = config.power["NORMAL"] if led_name == "power" else OFF

    return Resolution(looks=looks, sources=sources, contributors=contributors, problems=problems)


def diff(previous, current):
    """LED names whose value in `current` differs from `previous`."""
    return [led for led, value in current.items() if previous.get(led) != value]
