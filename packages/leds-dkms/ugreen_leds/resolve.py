# SPDX-License-Identifier: MIT
"""Pick one state per LED from what producers published."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Resolution:
    states: dict  # led -> winning state name, for every configured LED
    contributors: dict  # led -> [(producer, state)] that passed validation
    problems: list  # human-readable messages about ignored entries


def parse_state(raw):
    """Return the state name in a producer file, or None if malformed."""
    words = raw.split()
    return words[0] if len(words) == 1 else None


def resolve(scanned, config):
    """Apply priorities to `scanned` ({led: [(producer, raw)]}).

    The valid state with the highest priority wins; ties go to the producer
    whose name sorts first. LEDs without valid entries get their default.
    """
    states, contributors, problems = {}, {}, []

    for led_name in scanned:
        if led_name not in config.leds:
            for producer, _ in scanned[led_name]:
                problems.append(f"{producer}/{led_name}: unknown LED")

    for led_name, led in config.leds.items():
        valid = []
        for producer, raw in scanned.get(led_name, []):
            state = parse_state(raw)
            if state is None:
                problems.append(f"{producer}/{led_name}: malformed content {raw!r}")
            elif state not in config.states:
                problems.append(f"{producer}/{led_name}: unknown state {state!r}")
            else:
                valid.append((producer, state))
        contributors[led_name] = valid
        if valid:
            producer, state = min(
                valid, key=lambda item: (-config.states[item[1]].priority, item[0])
            )
            states[led_name] = state
        else:
            states[led_name] = led.default

    return Resolution(states=states, contributors=contributors, problems=problems)


def diff(previous, current):
    """LED names whose state in `current` differs from `previous`."""
    return [led for led, state in current.items() if previous.get(led) != state]
