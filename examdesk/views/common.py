from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from django.contrib import messages
from django.db.models import Model, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme

from .. import services
from ..labels import natural
from ..models import Clarification, Exam, Report


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


def suggestions(exam: Exam) -> dict[str, Any]:
    reports = Report.objects.of_exam(exam)
    return {
        "labels": sorted(set(exam.clarifications.values_list("label", flat=True)), key=natural),
        "venues": reports.values_list("venue", flat=True).distinct().order_by("venue"),
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
