"""Normalizing and ordering free text: question labels, places and names."""

import re
from typing import Any

GENERAL = "General"
_LABEL = re.compile(r"(?:q(?:uestion)?)?(\d+)([a-z]*)")
_TOP_LEVEL = re.compile(r"Q\d+")


def normalize_label(raw: str) -> str:
    """`3(b)` -> `Q3b`, `q3 b ii` -> `Q3bii`; blank or `general` -> `General`."""
    compact = re.sub(r"[\s()]", "", raw.lower())
    if compact in ("", GENERAL.lower()):
        return GENERAL
    if match := _LABEL.fullmatch(compact):
        return f"Q{match[1]}{match[2]}"
    raise ValueError(f"Not a question: {raw}. Use e.g. 3b, Q3(b)(ii) or General.")


def normalize_place(raw: str) -> str:
    """`lt 19` -> `LT19`."""
    return "".join(raw.split()).upper()


def normalize_name(raw: str) -> str:
    """` ann  lee ` -> `ann lee`."""
    return " ".join(raw.split())


def top_level(label: str) -> str:
    """`Q3bii` -> `Q3`."""
    match = _TOP_LEVEL.match(label)
    return match[0] if match else label


def covers(query: str, label: str) -> bool:
    """`Q3` covers `Q3` and `Q3b`, but not `Q31`."""
    rest = label.removeprefix(query)
    return label.startswith(query) and (rest == "" or rest.isalpha())


def natural(text: str) -> list[Any]:
    """Sort key putting B2 before B14."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]
