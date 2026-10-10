# SPDX-License-Identifier: MIT
"""ugreen-dxp-led: publish, clear and inspect LED contributions."""
import argparse
import json
import sys

from .config import CONTRIBUTED, ConfigError, add_config_option, load_config
from .contribution import parse_look
from .daemon import STATUS_PATH
from .tree import RUN_ROOT, clear, producer_dir, publish


def _label(look):
    """How a look reads in status output: its state, else its colour and effect."""
    if look["state"]:
        return look["state"]
    return look["color"] if look["effect"] == "none" else f"{look['color']}/{look['effect']}"


def format_status(data):
    lines = []
    for name, info in data["leds"].items():
        if "look" in info:
            sources = ", ".join(f"{c['producer']}={_label(c)}" for c in info["contributors"])
            line = f"{name:<8} {_label(info['look']):<14} {sources or '(default)'}"
        else:
            line = f"{name:<8} {info['device_name']:<14} (network traffic)"
        if not info["present"]:
            line += "  [absent]"
        elif not info["applied"]:
            line += "  [not applied]"
        lines.append(line)
        lines += [f"         error: {error}" for error in info["errors"]]
    lines += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(lines)


def show_status(path):
    try:
        with open(path) as f:
            print(format_status(json.load(f)))
    except (OSError, ValueError) as e:
        print(f"daemon status unavailable: {e}", file=sys.stderr)
        return 1
    return 0


def _look(args):
    """The Look that `set` publishes, or None after printing why there is none."""
    if len(args.look) == 1 and "=" not in args.look[0]:
        name = args.look[0]
        if args.led != "power":
            print("named looks exist only for the power LED; "
                  "give priority=N color=#rrggbb instead", file=sys.stderr)
            return None
        try:
            config = load_config(args.config)
        except ConfigError as e:
            print(f"invalid configuration: {e}", file=sys.stderr)
            return None
        if name not in config.power:
            print(f"unknown power look {name!r}; known: {', '.join(config.power)}",
                  file=sys.stderr)
            return None
        return config.power[name]
    try:
        return parse_look(" ".join(args.look))
    except ValueError as e:
        print(f"invalid look: {e}", file=sys.stderr)
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ugreen-dxp-led",
                                     description="Publish or inspect UGREEN DXP LED contributions")
    add_config_option(parser)
    parser.add_argument("--run-root", default=RUN_ROOT)
    parser.add_argument("--status-file", default=STATUS_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    set_cmd = commands.add_parser(
        "set", help="publish a look for an LED",
        description="LOOK is the name of a [power.*] look (power LED only) or "
                    "priority=N color=#rrggbb [effect=...] [state=...]")
    set_cmd.add_argument("led")
    set_cmd.add_argument("look", nargs="+")
    clear_cmd = commands.add_parser("clear", help="withdraw this producer's look for an LED")
    clear_cmd.add_argument("led")
    commands.add_parser("status", help="show what the daemon is displaying")
    args = parser.parse_args(argv)

    if args.command == "status":
        return show_status(args.status_file)
    if args.led not in CONTRIBUTED:
        reason = ("the network LED is driven by the daemon" if args.led == "netdev"
                  else "unknown LED")
        print(f"{args.led!r}: {reason}; LEDs: {', '.join(CONTRIBUTED)}", file=sys.stderr)
        return 2
    look = None
    if args.command == "set":
        look = _look(args)
        if look is None:
            return 2

    directory = producer_dir(args.run_root)
    try:
        if args.command == "set":
            publish(directory, args.led, look)
        else:
            clear(directory, args.led)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0
