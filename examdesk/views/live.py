"""The stream of exam versions each page listens to, and the refreshes of its live parts."""

import asyncio
import math
import time
from collections.abc import AsyncIterator
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connections
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils.cache import patch_vary_headers
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .. import session
from ..access import HtmxRequest, find_member
from ..listener import listener
from ..models import Clarification, Member, Report
from .common import readout_status, to_read_out

REFRESH_ELEMENT = "div#refresh"
"""The `HX-Source` of a refresh: the hidden element in `base.html`."""


def is_refresh(request: HtmxRequest) -> bool:
    return request.htmx.source == REFRESH_ELEMENT


def render_live(
    request: HtmxRequest,
    member: Member,
    template: str,
    context: dict[str, Any],
    partial: str | None = "live",
    path: str | None = None,
) -> HttpResponse:
    """Render the page, or for a refresh only its live parts: `partial` (None for a page without
    one) and the tab bar or nav badges.

    `path` sets the page's URL when a POST re-renders it.
    """
    refresh = is_refresh(request)
    context |= {
        "member": member,
        "page_url": path or request.get_full_path(),
        "is_refresh": refresh,
        "live_partial": f"{template}#{partial}" if partial else None,
        **navigation(request, member, context),
    }
    if refresh:
        response = render(request, "examdesk/includes/refresh.html", context)
    else:
        response = render(request, template, context)
    patch_vary_headers(response, ["HX-Request"])  # caches the page and its refresh apart
    return response


def navigation(request: HtmxRequest, member: Member, context: dict[str, Any]) -> dict[str, Any]:
    """The tab bar's context on invigilator screens, else the nav badges' counts."""
    if context.get("invigilator_screen"):
        announcements = list(to_read_out(member))
        snoozed_until = session.snoozed(request.session, member.exam)
        now = time.time()
        due = [a for a in announcements if snoozed_until.get(a.pk, 0) <= now]
        alert = due[0] if due and context["tab"] != "announcements" else None
        snooze_ends = [
            snoozed_until[a.pk] for a in announcements if snoozed_until.get(a.pk, 0) > now
        ]
        reports = Report.objects.in_venue_of(member)
        counts = {
            "open_clarifications": reports.open().count(),
            # Seats, as the To deliver tab lists them, not their questions.
            "to_deliver": reports.to_deliver().values("venue", "seat").distinct().count(),
            "announcements": len(announcements),
        }
        return {
            "counts": counts,
            "alert": alert,
            # Changes when a put-off alert returns, so that it vibrates again.
            "alert_key": alert and f"{alert.pk}.{int(snoozed_until.get(alert.pk, 0))}",
            "snooze_ends_in": math.ceil(min(snooze_ends) - now) if snooze_ends else None,
        }
    if member.is_examiner:
        counts = {
            "open_clarifications": member.exam.clarifications.in_state(
                Clarification.State.OPEN
            ).count(),
            "not_read_out": sum(1 for _, venues in readout_status(member.exam) if venues),
        }
        return {"counts": counts}
    return {}


@require_GET
@never_cache
async def events(request: HttpRequest, exam_id: int) -> StreamingHttpResponse:
    """Server-sent events: each exam version newer than the browser's (`Last-Event-ID`, else
    `?version=`), and a heartbeat after `EXAMDESK_HEARTBEAT_EVERY` without one."""
    last_event_id = request.headers.get("Last-Event-ID") or request.GET.get("version", "")
    last_version = int(last_event_id) if last_event_id.isdigit() else 0
    response = StreamingHttpResponse(
        _event_stream(request, exam_id, last_version), content_type="text/event-stream"
    )
    response["X-Accel-Buffering"] = "no"  # stops nginx buffering the events
    return response


async def _event_stream(
    request: HttpRequest, exam_id: int, last_version: int
) -> AsyncIterator[bytes]:
    subscription = listener.subscribe(exam_id)
    try:
        await listener.ready()
        member = await _member(request, exam_id)
        if member is None:  # the page refreshes and shows why
            yield _changed_event(last_version + 1)
            return
        subscription.member_id = member.pk
        version: int | None = member.exam.version
        heartbeat_every = settings.EXAMDESK_HEARTBEAT_EVERY.total_seconds()
        while version is not None:
            if version > last_version:
                last_version = version
                yield _changed_event(version)
            try:
                version = await asyncio.wait_for(subscription.versions.get(), heartbeat_every)
            except TimeoutError:
                yield b"data:\n\n"
    finally:
        listener.unsubscribe(subscription)


@sync_to_async
def _member(request: HttpRequest, exam_id: int) -> Member | None:
    """The device's member, read once listening. Closes the thread's database connection, which
    would otherwise stay open for as long as the stream."""
    try:
        return find_member(request, exam_id)[1]
    finally:
        connections.close_all()


def _changed_event(version: int) -> bytes:
    return f"id: {version}\nevent: changed\ndata:\n\n".encode()
