"""A seat in a page's query string."""

from django.http import HttpRequest
from django.urls import reverse
from django.utils.http import urlencode

from .labels import normalize_place


def seat_url(view: str, exam_id: int, venue: str, seat: str, **params: str | int) -> str:
    """The URL of a seat page; empty `params` are left out."""
    query = {"venue": venue, "seat": seat} | {k: v for k, v in params.items() if v}
    return f"{reverse(view, args=[exam_id])}?{urlencode(query)}"


def seat_from(request: HttpRequest) -> tuple[str, str]:
    get = request.GET.get
    return normalize_place(get("venue", "")), normalize_place(get("seat", ""))
