"""Polling: each page's `live` partial, re-rendered only when the exam version changed."""

import time
from functools import wraps
from typing import Any

from django.http import HttpResponse, HttpResponseBase
from django.shortcuts import render

from ..access import HtmxRequest, View
from ..models import Exam, Member


def poll_key(exam: Exam) -> str:
    """Changes with the exam version, and every minute so that wait times stay fresh."""
    return f"{exam.version}.{int(time.time() // 60)}"


def polled(view: View) -> View:
    """Answer an unchanged poll with 204, which htmx ignores."""

    @wraps(view)
    def wrapper(request: HtmxRequest, member: Member, **kwargs: Any) -> HttpResponseBase:
        if request.htmx and request.GET.get("v") == poll_key(member.exam):
            return HttpResponse(status=204)
        return view(request, member, **kwargs)

    return wrapper


def render_live(
    request: HtmxRequest,
    member: Member,
    template: str,
    context: dict[str, Any],
    partial: str = "live",
    path: str | None = None,
) -> HttpResponse:
    """Render the page, or only its `partial` for a poll (an htmx request with `v`).

    `path` sets the poll URL when a POST re-renders a page.
    """
    query = request.GET.copy()
    query["v"] = poll_key(member.exam)
    poll_url = f"{path or request.path}?{query.urlencode()}"
    context |= {"member": member, "poll_url": poll_url}
    poll = request.htmx and "v" in request.GET
    return render(request, f"{template}#{partial}" if poll else template, context)


def nav_badge(
    request: HtmxRequest, member: Member, name: str, value: dict[str, int]
) -> HttpResponse:
    """The polled badge `name` in the examiner's tabs."""
    return render_live(request, member, "examdesk/includes/nav.html", {name: value}, partial=name)
