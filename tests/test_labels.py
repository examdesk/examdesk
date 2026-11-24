import pytest

from examdesk.labels import (
    covers,
    natural,
    normalize_label,
    normalize_name,
    normalize_place,
    place_order,
    top_level,
)


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("3(b)", "Q3b"),
        ("q3 b ii", "Q3bii"),
        ("Q3", "Q3"),
        (" 12 (a)(iv) ", "Q12aiv"),
        ("Question 4c", "Q4c"),
        ("general", "General"),
        ("GENERAL", "General"),
        ("", "General"),
    ],
)
def test_normalize_label(raw: str, label: str) -> None:
    assert normalize_label(raw) == label


@pytest.mark.parametrize("raw", ["b", "3.1", "Q", "abc 3"])
def test_normalize_label_rejects_unknown_forms(raw: str) -> None:
    with pytest.raises(ValueError, match="question"):
        normalize_label(raw)


@pytest.mark.parametrize(("raw", "place"), [(" 14 ", "14"), ("hall 1", "HALL1")])
def test_normalize_place(raw: str, place: str) -> None:
    assert normalize_place(raw) == place


def test_top_level() -> None:
    assert top_level("Q3bii") == "Q3"
    assert top_level("Q12") == "Q12"
    assert top_level("General") == "General"


def test_covers() -> None:
    assert covers("Q3", "Q3")
    assert covers("Q3", "Q3b")
    assert covers("Q3b", "Q3bii")
    assert not covers("Q3", "Q31")
    assert not covers("Q3b", "Q3c")
    assert not covers("Q3", "General")


def test_natural_puts_b2_before_b14() -> None:
    assert sorted(["14", "2", "10", "9"], key=natural) == ["2", "9", "10", "14"]


def test_place_order_sorts_by_venue_then_seat() -> None:
    places = [("HALL2", "1"), ("HALL1", "14"), ("HALL1", "2")]
    assert sorted(places, key=place_order) == [("HALL1", "2"), ("HALL1", "14"), ("HALL2", "1")]


def test_normalize_name_collapses_spaces() -> None:
    assert normalize_name("  ann   lee ") == "ann lee"
