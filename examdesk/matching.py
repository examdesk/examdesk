"""Clarifications that may ask the same thing."""

from collections.abc import Iterable, Sequence

from django.conf import settings

from .labels import covers, top_level
from .models import Clarification
from .similarity import best_similarity


def rank_similar(
    texts: Sequence[str], clarifications: Iterable[Clarification]
) -> list[tuple[Clarification, float]]:
    """Each clarification with its similarity to `texts`, most similar first.

    Prefetch the clarifications' `reports`.
    """
    scored = [(c, best_similarity(texts, c.texts)) for c in clarifications]
    return sorted(scored, key=lambda pair: pair[1], reverse=True)


def same_question(label: str, other: str) -> bool:
    """`other` is under the top-level question of `label`."""
    return covers(top_level(label), other)


def similar_enough(score: float) -> bool:
    return score >= settings.EXAMDESK_SIMILARITY_THRESHOLD
