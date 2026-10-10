# SPDX-License-Identifier: MIT
"""Change keys in TOML text while keeping its comments and layout.

Used by the configuration migrations, which start from the shipped template.
Arrays must be written on one line: a line starting with `[` is a table header.
"""
import json
import re

_HEADER = re.compile(r"\s*\[([^\[\]]+)\]\s*(#.*)?$")


def toml_value(value):
    """`value` (bool, int, float, str or list of those) as a TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # a JSON string is a valid TOML basic string
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TypeError(f"cannot write {value!r} as TOML")


def _table_body(lines, table):
    """(first, end) indexes of the lines inside `table` (None: the top level)."""
    first = 0
    if table is not None:
        for index, line in enumerate(lines):
            match = _HEADER.match(line)
            if match and match.group(1).strip() == table:
                first = index + 1
                break
        else:
            raise KeyError(f"no [{table}] table")
    end = next((i for i in range(first, len(lines)) if _HEADER.match(lines[i])), len(lines))
    return first, end


def set_key(text, table, key, value):
    """Return `text` with `key = value` in `[table]` (None: the top level).

    Replaces the first line of that table that sets the key, commented out or
    not; otherwise inserts the line right after the table header (top level:
    at the start). Raises KeyError if the table has no header.
    """
    lines = text.splitlines(keepends=True)
    first, end = _table_body(lines, table)
    line = f"{key} = {toml_value(value)}\n"
    pattern = re.compile(rf"#?\s*{re.escape(key)}\s*=")
    for index in range(first, end):
        if pattern.match(lines[index].lstrip()):
            lines[index] = line
            return "".join(lines)
    lines.insert(first, line)
    return "".join(lines)
