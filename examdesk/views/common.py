import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine, Iterable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any, Literal

import openai
from asgiref.sync import sync_to_async
from django.contrib import messages
from django.db.models import Model, Prefetch, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme

from .. import services
from ..access import HtmxRequest, admit
from ..labels import natural
from ..models import Announcement, Clarification, Exam, Member, Readout, Report

logger = logging.getLogger(__name__)

type AsyncView = Callable[..., Coroutine[Any, Any, HttpResponseBase]]
type AIModelField = Literal["ai_vision_model", "ai_fast_model", "ai_careful_model"]


def ai_view(
    model: AIModelField, *, examiner_only: bool = False, live_only: bool = False
) -> Callable[[AsyncView], AsyncView]:
    """An async `exam_view` for exams with `model` chosen, answering 502 when the AI service fails.

    The view closes its database connections before waiting on the service.
    """

    def decorator(view: AsyncView) -> AsyncView:
        @wraps(view)
        async def wrapper(request: HtmxRequest, exam_id: int, **kwargs: Any) -> HttpResponseBase:
            member = await sync_to_async(admit)(
                request, exam_id, examiner_only=examiner_only, live_only=live_only
            )
            if not isinstance(member, Member):
                return member
            if not getattr(member.exam, model):
                raise Http404
            try:
                return await view(request, member, **kwargs)
            except openai.APIError as error:
                logger.warning("%s failed: %s", view.__name__, error)
                return HttpResponse(status=502)

        return wrapper

    return decorator


@contextmanager
def flash_errors(request: HttpRequest) -> Iterator[None]:
    """Turn a ServiceError into an error message."""
    try:
        yield
    except services.ServiceError as error:
        messages.error(request, str(error))


def redirect_next(request: HttpRequest, default: str) -> HttpResponse:
    target = request.POST.get("next") or request.GET.get("next", "")
    safe = url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure())
    return redirect(target if safe else default)


def pick[M: Model](queryset: QuerySet[M], pk: str | None) -> M | None:
    return queryset.filter(pk=pk).first() if pk and pk.isdigit() else None


def get_clarification(exam: Exam, clarification_id: int) -> Clarification:
    clarifications = exam.clarifications.select_related("current_answer__member", "merged_into")
    return get_object_or_404(clarifications, pk=clarification_id)


def known_venues(exam: Exam) -> list[str]:
    """The exam's venues; when not locked, also those that reports came from."""
    if exam.venues_locked:
        return exam.venues
    report_venues = Report.objects.of_exam(exam).values_list("venue", flat=True)
    return sorted({*exam.venues, *report_venues}, key=natural)


def suggestions(exam: Exam) -> dict[str, Any]:
    """The datalists' options. `labels` and `venues` are lazy, as refreshes don't render the
    datalists."""
    reports = Report.objects.of_exam(exam)
    labels = exam.clarifications.exclude(label="").values_list("label", flat=True)
    return {
        "labels": lambda: sorted({*labels, *exam.question_labels}, key=natural),
        "venues": lambda: known_venues(exam),
        "seats": reports.values_list("seat", flat=True).distinct().order_by("seat"),
    }


def back_to_report(request: HttpRequest) -> str:
    """`"report"` if a delivery started from the Report tab, else `""` for To deliver."""
    data = request.POST if request.method == "POST" else request.GET
    return "report" if data.get("back") == "report" else ""


def group_by_venue[T](pairs: Iterable[tuple[str, T]]) -> list[tuple[str, list[T]]]:
    """Group items by venue in natural order, no venue last, keeping their order within."""
    groups: defaultdict[str, list[T]] = defaultdict(list)
    for venue, item in pairs:
        groups[venue].append(item)
    return sorted(groups.items(), key=lambda group: (not group[0], natural(group[0])))


def to_read_out(member: Member) -> QuerySet[Announcement]:
    """Not yet read out in the member's venue; all of them if none is set."""
    announcements = member.exam.announcements.all()
    return announcements.exclude(readouts__venue=member.venue) if member.venue else announcements


def readout_status(exam: Exam) -> list[tuple[Announcement, list[str]]]:
    """Each announcement with the known venues it has not been read out in yet."""
    readouts = Prefetch("readouts", Readout.objects.select_related("member"))
    announcements = list(exam.announcements.select_related("member").prefetch_related(readouts))
    venues = {*known_venues(exam), *(r.venue for a in announcements for r in a.readouts.all())}
    return [(a, sorted(venues - {r.venue for r in a.readouts.all()})) for a in announcements]
