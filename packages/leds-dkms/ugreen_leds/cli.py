# SPDX-License-Identifier: MIT
"""ugreen-dxp-led: publish, clear and inspect LED states."""
import argparse
import json
import sys

from .config import ConfigError, add_config_option, load_config
from .daemon import STATUS_PATH
from .tree import RUN_ROOT, clear, producer_dir, publish


def format_status(data):
    lines = []
    for name, info in data["leds"].items():
        sources = ", ".join(f"{c['producer']}={c['state']}" for c in info["contributors"])
        line = f"{name:<8} {info['state']:<14} {sources or '(default)'}"
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ugreen-dxp-led",
                                     description="Publish or inspect UGREEN DXP LED states")
    add_config_option(parser)
    parser.add_argument("--run-root", default=RUN_ROOT)
    parser.add_argument("--status-file", default=STATUS_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    set_cmd = commands.add_parser("set", help="publish a state for an LED")
    set_cmd.add_argument("led")
    set_cmd.add_argument("state")
    clear_cmd = commands.add_parser("clear", help="withdraw this producer's state for an LED")
    clear_cmd.add_argument("led")
    commands.add_parser("status", help="show what the daemon is displaying")
    args = parser.parse_args(argv)

    if args.command == "status":
        return show_status(args.status_file)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"invalid configuration: {e}", file=sys.stderr)
        return 1
    if args.led not in config.leds:
        print(f"unknown LED {args.led!r}; known: {', '.join(config.leds)}", file=sys.stderr)
        return 2
    if args.command == "set" and args.state not in config.states:
        print(f"unknown state {args.state!r}; known: {', '.join(config.states)}", file=sys.stderr)
        return 2

    directory = producer_dir(args.run_root)
    try:
        if args.command == "set":
            publish(directory, args.led, args.state)
        else:
            clear(directory, args.led)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0
