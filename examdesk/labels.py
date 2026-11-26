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


_MAX_QUESTIONS_PER_RANGE = 200
_RANGE = re.compile(r"(.+?)\s*[-\N{EN DASH}]\s*(.+)")
_RANGE_BOUND = re.compile(r"Q(?P<number>\d+)(?P<part>[a-z]?)")


def parse_question_numbers(text: str) -> list[str]:
    """`1-3, 4a-c` -> `Q1`, `Q2`, `Q3`, `Q4a`, `Q4b`, `Q4c`, in natural order."""
    labels: set[str] = set()
    for item in filter(None, (item.strip() for item in text.split(","))):
        if match := _RANGE.fullmatch(item):
            labels.update(_expand_range(match[1], match[2]))
        else:
            labels.add(normalize_label(item))
    return sorted(labels, key=natural)


def _expand_range(first: str, last: str) -> list[str]:
    """`1`, `40` -> `Q1` to `Q40`; `3a`, `d` or `3a`, `3d` -> `Q3a` to `Q3d`."""
    first_bound = _RANGE_BOUND.fullmatch(normalize_label(first))
    if first_bound and last.isalpha():
        last = first_bound["number"] + last
    last_bound = _RANGE_BOUND.fullmatch(normalize_label(last))
    not_a_range = ValueError(f"Not a range: {first}-{last}. Use e.g. 1-40 or 3a-d.")
    if not (first_bound and last_bound):
        raise not_a_range
    first_number, first_part = first_bound.groups()
    last_number, last_part = last_bound.groups()
    if not first_part and not last_part:
        numbers = range(int(first_number), int(last_number) + 1)
        if len(numbers) > _MAX_QUESTIONS_PER_RANGE:
            raise ValueError(f"Too many questions in {first}-{last}.")
        if numbers:
            return [f"Q{number}" for number in numbers]
    elif first_number == last_number and first_part and last_part:
        if codepoints := range(ord(first_part), ord(last_part) + 1):
            return [f"Q{first_number}{chr(codepoint)}" for codepoint in codepoints]
    raise not_a_range


_PART = r"(?:\s?\([a-h]\)|[a-h])"
# `Q3`, `Q3a`, `question 3` or `3a`, `3(a)` in free text
_MENTION = re.compile(rf"\b(?:q(?:uestion)?\s?\d+{_PART}?|\d+{_PART})(?!\w)", re.IGNORECASE)


def mismatched_label(label: str, text: str) -> str | None:
    """A question `text` mentions, unless one it mentions covers or is covered by `label`."""
    mentions = [normalize_label(mention) for mention in _MENTION.findall(text)]
    if any(covers(label, mention) or covers(mention, label) for mention in mentions):
        return None
    return mentions[0] if mentions else None


def normalize_place(raw: str) -> str:
    """`hall 1` -> `HALL1`."""
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
    """Sort key putting 2 before 14."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def place_order(place: tuple[str, str]) -> tuple[list[Any], list[Any]]:
    """Sort key putting HALL1 2 before HALL1 14 and HALL2 1."""
    return natural(place[0]), natural(place[1])
