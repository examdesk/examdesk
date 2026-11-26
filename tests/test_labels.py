import pytest

from examdesk.labels import (
    covers,
    mismatched_label,
    natural,
    normalize_label,
    normalize_name,
    normalize_place,
    parse_question_numbers,
    parse_seats,
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


@pytest.mark.parametrize(
    ("raw", "labels"),
    [
        ("1-3", ["Q1", "Q2", "Q3"]),
        ("2, 1a-c, Q3(b)", ["Q1a", "Q1b", "Q1c", "Q2", "Q3b"]),
        ("4b \N{EN DASH} 4d, 1, 1,", ["Q1", "Q4b", "Q4c", "Q4d"]),
        ("general, 2", ["General", "Q2"]),
        ("", []),
    ],
)
def test_parse_question_numbers(raw: str, labels: list[str]) -> None:
    assert parse_question_numbers(raw) == labels


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("1, x", "Not a question: x"),
        ("3-1", "Not a range: 3-1"),
        ("1a-2c", "Not a range: 1a-2c"),
        ("1-3b", "Not a range: 1-3b"),
        ("1-1000", "Too many questions in 1-1000"),
    ],
)
def test_parse_question_numbers_rejects(raw: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        parse_question_numbers(raw)


@pytest.mark.parametrize(
    ("raw", "seats"),
    [
        ("1-3", ["1", "2", "3"]),
        ("a1 - a3, 10, b 2-3", ["10", "A1", "A2", "A3", "B2", "B3"]),
        ("A9\N{EN DASH}11, 9,", ["9", "A9", "A10", "A11"]),
        ("", []),
    ],
)
def test_parse_seats(raw: str, seats: list[str]) -> None:
    assert parse_seats(raw) == seats


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("3-1", "Not a range: 3-1"),
        ("A1-B3", "Not a range: A1-B3"),
        ("1-A3", "Not a range: 1-A3"),
        ("1-2000", "Too many seats in 1-2000"),
        ("x" * 31, "Too long: X{31}"),
    ],
)
def test_parse_seats_rejects(raw: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        parse_seats(raw)


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


@pytest.mark.parametrize(
    ("label", "text", "mentioned"),
    [
        ("General", "There is no Figure 2. Which graph is 5b about?", "Q5b"),
        ("General", "5(b) mentions Figure 2", "Q5b"),
        ("Q4", "Question 6: can we use recursion?", "Q6"),
        ("Q2", "Q3 (a) says 8 integers", "Q3a"),
        ("Q5", "5b refers to Figure 2", None),  # covers it
        ("Q5b", "Is Q5 about Figure 1?", None),
        ("Q3a", "Q3 asks for 8 integers but the array has 9", None),
        ("General", "Do we have to answer all 8 questions in 2 hours?", None),
        ("General", "Is it 2(n-1) or O(n log n)?", None),
        ("Q1", "Can I leave at 10am?", None),
    ],
)
def test_mismatched_label(label: str, text: str, mentioned: str | None) -> None:
    assert mismatched_label(label, text) == mentioned
