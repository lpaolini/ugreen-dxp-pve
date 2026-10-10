# SPDX-License-Identifier: MIT
"""Read the KEY=VALUE files that 0.9.10 used for configuration."""


def read_env(path):
    """{KEY: value} from a systemd EnvironmentFile.

    Blank lines, comments and lines without `=` are skipped; one pair of
    surrounding quotes is removed from the value.
    """
    values = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key.strip()] = value
    return values
