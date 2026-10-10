# SPDX-License-Identifier: MIT
"""Read the KEY=VALUE files that 0.9.10 used for configuration."""
import re


def read_env(path):
    """{KEY: value} from a systemd EnvironmentFile.

    Blank lines, comments and lines without `=` are skipped; one pair of
    surrounding quotes is removed from the value, and an unquoted value
    ends at a whitespace-then-`#` comment, as in the shell.
    """
    values = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if value[:1] in ("'", '"') and value[0] in value[1:]:
                value = value[1:value.index(value[0], 1)]
            else:
                value = re.split(r"\s#", value, maxsplit=1)[0].rstrip()
            values[key.strip()] = value
    return values
