"""The `exam_view` decorator: who is viewing an exam, and may they."""

from collections.abc import Callable
from functools import wraps
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.models import User
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django_htmx.http import HttpResponseClientRefresh
from django_htmx.middleware import HtmxDetails

from . import session
from .models import Exam, Member


class HtmxRequest(HttpRequest):
    htmx: HtmxDetails


class StaffRequest(HttpRequest):
    user: User


def forbidden(request: HttpRequest, text: str) -> HttpResponse:
    return render(request, "examdesk/forbidden.html", {"text": text}, status=403)


def find_member(request: HttpRequest, exam_id: int) -> tuple[Exam | None, Member | None]:
    """The exam and this device's member of it, in one query when it has one."""
    if pk := session.member_id(request.session, exam_id):
        members = Member.objects.active().select_related("exam")
        member = members.filter(pk=pk, exam_id=exam_id).first()
        if member:
            return member.exam, member
    return Exam.objects.filter(pk=exam_id).first(), None


def note_presence(member: Member) -> None:
    now = timezone.now()
    if now - member.last_seen >= settings.EXAMDESK_SEEN_EVERY:
        Member.objects.filter(pk=member.pk).update(last_seen=now)
        member.last_seen = now


type View = Callable[..., HttpResponseBase]


def exam_view(*, examiner_only: bool = False, live_only: bool = False) -> Callable[[View], View]:
    """Resolve `exam_id` to this device's `Member` and check access.

    Closed exams admit only examiners, and `live_only` views not at all. An owner
    without a member goes to the examiner link to pick a name.
    """

    def decorator(view: View) -> View:
        @wraps(view)
        def wrapper(request: HtmxRequest, exam_id: int, **kwargs: Any) -> HttpResponseBase:
            exam, member = find_member(request, exam_id)
            if member is None:
                if request.htmx:
                    return HttpResponseClientRefresh()
                if exam and exam.owner_id == request.user.pk:
                    join = reverse("join", args=[exam.examiner_token])
                    return redirect(f"{join}?{urlencode({'next': request.get_full_path()})}")
                raise Http404("Open the exam link first.")
            if examiner_only and not member.is_examiner:
                return forbidden(request, "Only examiners can do this.")
            if not member.exam.is_live and (live_only or not member.is_examiner):
                if request.htmx:
                    return HttpResponseClientRefresh()
                return forbidden(request, "This exam is closed.")
            note_presence(member)
            return view(request, member, **kwargs)

        return wrapper

    return decorator
