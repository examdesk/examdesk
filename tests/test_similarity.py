import pytest

from examdesk.similarity import similarity


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Is x an integer?", "is X an integer or a real number"),
        ("Can we assume n > 0?", "may we assume n is positive"),
        ("Can the array have duplicate elements?", "Does the array contain duplicates?"),
        ("Can we use a hashmap?", "Are we allowed to use hash maps"),
    ],
)
def test_same_meaning(a: str, b: str) -> None:
    assert similarity(a, b) == pytest.approx(similarity(b, a))
    assert similarity(a, b) >= 0.75


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Is x an integer?", "Is y real?"),
        ("Is the heap a min heap?", "Is the array sorted?"),
        ("What is the time limit for part b", "Is there a typo in the equation"),
    ],
)
def test_different_meaning(a: str, b: str) -> None:
    assert similarity(a, b) < 0.5


@pytest.mark.parametrize(("a", "b"), [("Is x an integer?", ""), ("", " "), ("x", "x")])
def test_blank_is_unlike_anything_and_text_is_like_itself(a: str, b: str) -> None:
    assert similarity(a, b) == pytest.approx(1.0 if a == b else 0.0, abs=1e-6)
