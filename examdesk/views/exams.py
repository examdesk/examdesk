"""The list of exams, joining and leaving one, names, and what both roles share."""

from collections import Counter

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .. import services, session
from ..access import HtmxRequest, StaffRequest, exam_view, forbidden
from ..forms import ExamForm, NameForm
from ..models import Clarification, Exam, Member, Report, Role
from .common import flash_errors, redirect_next


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    visible = Exam.objects.filter(
        members__in=session.joined_member_ids(request.session), members__removed=False
    )
    if request.user.is_authenticated:
        visible |= Exam.objects.filter(owner_id=request.user.pk)
    exams = list(visible.distinct().order_by("-created_at"))
    if not exams and not request.user.is_authenticated:
        return render(request, "examdesk/landing.html")
    open_counts = dict(
        Clarification.objects.in_state(Clarification.State.OPEN)
        .filter(exam__in=exams)
        .values_list("exam")
        .annotate(Count("pk"))
        .order_by()
    )
    seats = (
        Report.objects.to_deliver()
        .filter(clarification__exam__in=exams)
        .values_list("clarification__exam", "venue", "seat")
        .order_by()
        .distinct()
    )
    to_deliver = Counter(exam_id for exam_id, _, _ in seats)
    cards = [(exam, open_counts.get(exam.pk, 0), to_deliver[exam.pk]) for exam in exams]
    return render(request, "examdesk/home.html", {"cards": cards})


def staff_name(request: HttpRequest) -> str:
    user = request.user
    return (user.get_full_name() or user.get_username()) if user.is_authenticated else ""


@require_POST
@login_required
def create_exam(request: StaffRequest) -> HttpResponse:
    form = ExamForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Name the exam.")
        return redirect("home")
    exam = services.create_exam(name=form.cleaned_data["name"], owner=request.user)
    owner = services.add_member(exam, name=staff_name(request), role=Role.EXAMINER)
    session.remember(request.session, owner)
    messages.success(request, f"Created {exam.name}.")
    return redirect("examiner:settings", exam.pk)


def suggested_name(request: HttpRequest) -> str:
    """The name this device used last, or the staff member's."""
    ids = session.joined_member_ids(request.session)
    latest = Member.objects.filter(pk__in=ids).order_by("-last_seen").first()
    return latest.name if latest else staff_name(request)


@require_http_methods(["GET", "POST"])
def join(request: HttpRequest, token: str) -> HttpResponse:
    """Pick a name, or confirm being the member who already has it.

    The link sets the role. An invigilator cannot take an examiner's name.
    """
    exam = Exam.objects.filter(examiner_token=token).first()
    role = Role.EXAMINER
    if exam is None:
        exam, role = get_object_or_404(Exam, invigilator_token=token), Role.INVIGILATOR
    if exam.owner_id == request.user.pk:
        role = Role.EXAMINER
    if not exam.is_live and role == Role.INVIGILATOR:
        return forbidden(request, "This exam is closed.")
    exam_home = reverse("exam", args=[exam.pk])
    member = session.member(request.session, exam)
    form = NameForm(request.POST or None, initial={"name": suggested_name(request)})
    other = None
    if member is None and request.method == "POST" and form.is_valid():
        name = form.cleaned_data["name"]
        other = exam.members.filter(name__iexact=name).first()
        if other is None:
            member = services.add_member(exam, name=name, role=role)
        elif other.is_examiner and role == Role.INVIGILATOR:
            form.add_error("name", "An examiner on this exam uses that name.")
            other = None
        elif "same" in request.POST:
            member = other
    if member is None:
        return render(request, "examdesk/join.html", {"form": form, "other": other, "exam": exam})
    if member.removed:
        services.restore_member(member)
    services.promote(member, role)
    session.remember(request.session, member)
    return redirect_next(request, exam_home)


@require_POST
@exam_view()
def leave_exam(request: HtmxRequest, member: Member) -> HttpResponse:
    session.forget(request.session, member.exam)
    messages.success(request, f"Left {member.exam.name}.")
    return redirect("home")


@exam_view()
def rename_member(request: HtmxRequest, member: Member) -> HttpResponse:
    form = NameForm(request.POST or None, initial={"name": member.name})
    if request.method == "POST" and form.is_valid():
        try:
            services.rename_member(member, form.cleaned_data["name"])
        except services.ServiceError as error:
            form.add_error("name", str(error))
        else:
            return redirect_next(request, reverse("exam", args=[member.exam.pk]))
    return render(request, "examdesk/rename_member.html", {"form": form, "member": member})


@require_GET
@exam_view()
def exam_home(request: HtmxRequest, member: Member) -> HttpResponse:
    return redirect(
        "examiner:clarifications" if member.is_examiner else "invigilator:report", member.exam.pk
    )


@require_GET
@exam_view()
def photo(request: HtmxRequest, member: Member, report_id: int) -> HttpResponseBase:
    report = get_object_or_404(Report.objects.of_exam(member.exam), pk=report_id)
    if not report.photo:
        raise Http404("No photo.")
    response = FileResponse(report.photo.open("rb"))
    max_age = int(settings.EXAMDESK_PHOTO_CACHE_FOR.total_seconds())
    response["Cache-Control"] = f"private, max-age={max_age}"
    return response


@require_POST
@exam_view()
def delete_report(request: HtmxRequest, member: Member, report_id: int) -> HttpResponse:
    """Examiners go back to the list if the clarification was deleted with it."""
    report = get_object_or_404(
        Report.objects.of_exam(member.exam).select_related("clarification"), pk=report_id
    )
    with flash_errors(request):
        services.delete_report(report)
        messages.success(request, f"Deleted the report from {report.venue} {report.seat}.")
        gone = not Clarification.objects.filter(pk=report.clarification_id).exists()
        if gone and member.is_examiner:
            return redirect("examiner:clarifications", member.exam.pk)
    return redirect_next(request, reverse("invigilator:report", args=[member.exam.pk]))
