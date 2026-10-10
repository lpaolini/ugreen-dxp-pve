# SPDX-License-Identifier: MIT
"""Change keys in TOML text while keeping its comments and layout.

Used by the configuration migrations, which start from the shipped template.
Arrays must be written on one line: a line starting with `[` is a table header.
Arrays of tables (`[[...]]`) are not supported.
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
        # A JSON string is a valid TOML basic string, except that TOML also
        # forbids a raw DEL and rejects surrogate pairs (so no ensure_ascii).
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
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

    Replaces the first line of that table that sets the key; failing that the
    first commented-out `# key =` line; otherwise inserts the line right after the table header (top level:
    at the start). Raises KeyError if the table has no header.
    """
    lines = text.splitlines(keepends=True)
    first, end = _table_body(lines, table)
    line = f"{key} = {toml_value(value)}\n"
    key_re = re.escape(key)
    for pattern in (rf"{key_re}\s*=", rf"#\s*{key_re}\s*="):
        pattern = re.compile(pattern)
        for index in range(first, end):
            if pattern.match(lines[index].lstrip()):
                lines[index] = line
                return "".join(lines)
    if first and not lines[first - 1].endswith("\n"):
        lines[first - 1] += "\n"
    lines.insert(first, line)
    return "".join(lines)


def _table_starts(lines):
    """{table: index of its first line}, counting the comment lines right above the header."""
    starts = {}
    for index, line in enumerate(lines):
        match = _HEADER.match(line)
        if match:
            first = index
            while first > 0 and lines[first - 1].lstrip().startswith("#"):
                first -= 1
            starts[match.group(1).strip()] = first
    return starts


def add_missing_tables(text, template):
    """Return (text, added): `text` with every table of `template` it lacks appended.

    Each appended table keeps the comment lines right above its header in the
    template. `added` lists the appended table names in template order.
    """
    have = _table_starts(text.splitlines(keepends=True))
    lines = template.splitlines(keepends=True)
    starts = _table_starts(lines)
    firsts = sorted(starts.values())
    added = []
    for table, first in starts.items():
        if table in have:
            continue
        end = next((i for i in firsts if i > first), len(lines))
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n" + "".join(lines[first:end]).rstrip("\n") + "\n"
        added.append(table)
    return text, added
