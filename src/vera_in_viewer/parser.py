"""Parse VERAin XML files (Teuchos ParameterList format) into nested dicts.

Mirrors ``XMLVeraParser.js`` from VeraInView: every ``ParameterList`` becomes a
``dict`` keyed by its ``name`` and every ``Parameter`` is converted according to
its ``type`` attribute (``int``, ``double``, ``bool``, ``string`` or
``Array(<type>)``).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ARRAY_TYPE = re.compile(r"Array\((.*)\)")


def _to_bool(txt: str) -> bool:
    return txt.strip().lower() == "true"


def _to_int(txt: str) -> int | float:
    # Be lenient like JavaScript ``Number()``: "1.0" is accepted as an int.
    value = float(txt)
    return int(value) if value.is_integer() else value


PRIMITIVE_TYPES: dict[str, Callable[[str], Any]] = {
    "double": float,
    "int": _to_int,
    "bool": _to_bool,
    "string": str,
}


def convert(type_name: str, value: str) -> Any:
    """Convert a parameter ``value`` string according to its VERA ``type_name``."""
    match = _ARRAY_TYPE.fullmatch(type_name.strip())
    if match:
        converter = PRIMITIVE_TYPES.get(match.group(1), str)
        content = value.strip()
        if content.startswith("{") and content.endswith("}"):
            content = content[1:-1]
        if not content.strip():
            return []
        return [converter(item.strip()) for item in content.split(",")]

    return PRIMITIVE_TYPES.get(type_name, str)(value)


def _fill(container: dict[str, Any], element: ET.Element) -> None:
    for child in element:
        name = child.get("name")
        if name is None:
            continue
        if child.tag == "ParameterList":
            sub: dict[str, Any] = {}
            container[name] = sub
            _fill(sub, child)
        elif child.tag == "Parameter":
            container[name] = convert(child.get("type", "string"), child.get("value", ""))


def parse_string(content: str | bytes) -> dict[str, Any]:
    """Parse VERAin XML content and return the root dictionary.

    The result is keyed by the root ``ParameterList`` name (usually ``CASEID``).
    """
    root = ET.fromstring(content)
    result: dict[str, Any] = {}
    if root.tag == "ParameterList":
        top: dict[str, Any] = {}
        result[root.get("name", "CASEID")] = top
        _fill(top, root)
    else:
        _fill(result, root)
    return result


def parse_file(path: str | Path) -> dict[str, Any]:
    """Parse a VERAin XML file from disk."""
    return parse_string(Path(path).read_bytes())
