"""The examiner's screens (laptop)."""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import segno
from django.conf import settings
from django.contrib import messages
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_POST

from .. import services, session
from ..access import HtmxRequest, exam_view
from ..forms import (
    AnswerForm,
    ClarificationFilterForm,
    DeleteExamForm,
    LabelForm,
    TextForm,
    form_errors,
)
from ..labels import covers, natural, top_level
from ..matching import rank_similar, same_question, similar_enough
from ..models import Announcement, Clarification, Exam, Member, Readout, Report, Role
from ..seats import seat_from
from .common import (
    flash_errors,
    get_clarification,
    group_by_venue,
    pick,
    suggestions,
)
from .live import nav_badge, polled, render_live


@dataclass(frozen=True)
class ClarificationRow:
    clarification: Clarification
    reports: list[Report]
    unresolved: int
    since: datetime | None  # when the longest-waiting seat asked


# Wait is measured from the longest-waiting seat, so new reports don't bump a clarification.
SORT_KEYS: dict[str, Callable[[ClarificationRow], Any]] = {
    "question": lambda row: natural(row.clarification.label),
    "seats": lambda row: -row.unresolved,
    "state": lambda row: list(Clarification.State).index(row.clarification.state),
    "wait": lambda row: (row.since is None, row.since or row.clarification.created_at),
}
FIRST_ORDER = {
    "question": "ascending",
    "seats": "descending",
    "state": "ascending",
    "wait": "descending",
}


def sort_links(sort: str) -> dict[str, dict[str, str]]:
    """Each column header's `sort` and `aria-sort`. The sorted column's link reverses it."""
    links = {}
    flip = {"ascending": "descending", "descending": "ascending"}
    for column, first in FIRST_ORDER.items():
        order = first if sort == column else flip[first] if sort == f"-{column}" else ""
        links[column] = {"sort": f"-{column}" if sort == column else column, "order": order}
    return links


@require_GET
@exam_view(examiner_only=True)
@polled
def clarifications(request: HtmxRequest, member: Member) -> HttpResponse:
    """Restores this device's last filters when opened without a query."""
    exam = member.exam
    if not request.GET and (saved := session.filters(request.session, exam)):
        return redirect(f"{request.path}?{saved}")
    used = set(exam.clarifications.values_list("label", flat=True))
    # Offer top-level labels too: Q3 selects Q3a and Q3b.
    labels = {label for used_label in used for label in (used_label, top_level(used_label))}
    # A saved filter may name a label that was since relabeled or deleted.
    query = request.GET.copy()
    query.setlist("label", [label for label in query.getlist("label") if label in labels])
    if "v" not in query:  # not a poll
        session.save_filters(request.session, exam, query.urlencode())
    form = ClarificationFilterForm(query, labels=sorted(labels, key=natural), sorts=SORT_KEYS)
    filters = form.cleaned_data if form.is_valid() else {}
    state = filters.get("state") or Clarification.State.OPEN
    found = exam.clarifications.all()
    if state != "all":
        found = found.in_state(Clarification.State(state))
    if picked := filters.get("label"):
        found = found.filter(
            label__in=[label for label in used if any(covers(p, label) for p in picked)]
        )
    found = (
        found.with_photo()
        .select_related("current_answer")
        .prefetch_related(Prefetch("reports", Report.objects.with_state()))
    )
    rows = []
    for clarification in found:
        reports = list(clarification.reports.all())
        unresolved = [r.created_at for r in reports if r.state in Report.UNRESOLVED]
        rows.append(
            ClarificationRow(clarification, reports, len(unresolved), min(unresolved, default=None))
        )
    sort = filters.get("sort") or "wait"
    rows.sort(key=SORT_KEYS["wait"])
    rows.sort(key=SORT_KEYS[sort.removeprefix("-")], reverse=sort.startswith("-"))  # ties by wait
    filtered = state != "open" or bool(filters.get("label"))
    context = {
        "form": form,
        "rows": rows,
        "filtered": filtered,
        "sorts": sort_links(sort),
    }
    template = "examdesk/examiner/clarifications.html"
    return render_live(request, member, template, context | suggestions(exam))


@require_GET
@exam_view(examiner_only=True)
@polled
def clarifications_badge(request: HtmxRequest, member: Member) -> HttpResponse:
    count = member.exam.clarifications.in_state(Clarification.State.OPEN).count()
    return nav_badge(request, member, "clarifications_badge", {"open": count})


def clarification_page(
    request: HtmxRequest,
    member: Member,
    clarification_id: int,
    form: AnswerForm | None = None,
    label_form: LabelForm | None = None,
) -> HttpResponse:
    """A bound `label_form` reopens the relabel dialog with its errors."""
    clarification = get_clarification(member.exam, clarification_id)
    correcting = form is not None
    if form is None:
        current = clarification.current_answer
        form = AnswerForm(
            initial={
                "text": current.text if current else "",
                "expected_answer": current and current.pk,
            }
        )
    reports = list(
        Report.objects.with_state().filter(clarification=clarification).select_related("member")
    )
    related = list(
        member.exam.clarifications.exclude(pk=clarification.pk)
        .exclude(closed_reason=Clarification.ClosedReason.MERGED)
        .prefetch_related("reports")
    )
    current = clarification.current_answer
    context = {
        "clarification": clarification,
        # reports that add something to the wording
        "asked": [r for r in reports if r.photo or (r.text and r.text != clarification.wording)],
        "duplicates": [
            (other, round(score * 100))
            for other, score in rank_similar(clarification.texts, related)
            if same_question(clarification.label, other.label) or similar_enough(score)
        ],
        "form": form,
        "reports": reports,
        "answers": clarification.answers.select_related("member"),
        "withdrawable": current and not current.deliveries.exists(),
        "others": [other for other in related if not other.closed_reason],
        "canned": settings.EXAMDESK_CANNED_ANSWERS,
        "label_form": label_form or LabelForm(initial={"label": clarification.label}),
        "correcting": correcting,
        **suggestions(member.exam),
    }
    path = reverse("examiner:clarification", args=[member.exam.pk, clarification_id])
    template = "examdesk/examiner/clarification.html"
    response = render_live(request, member, template, context, path=path)
    response.status_code = 400 if label_form and label_form.errors else 200
    return response


@require_GET
@exam_view(examiner_only=True)
@polled
def clarification(request: HtmxRequest, member: Member, clarification_id: int) -> HttpResponse:
    return clarification_page(request, member, clarification_id)


@require_POST
@exam_view(examiner_only=True)
def answer_clarification(
    request: HtmxRequest, member: Member, clarification_id: int
) -> HttpResponse:
    form = AnswerForm(request.POST)
    if form.is_valid():
        text = form.cleaned_data["text"]
        clarification = get_clarification(member.exam, clarification_id)
        try:
            services.answer_clarification(
                clarification,
                text=text,
                member=member,
                expected_answer=form.cleaned_data["expected_answer"],
            )
            messages.success(request, "Sent.")
            return redirect("examiner:clarification", member.exam.pk, clarification_id)
        except services.AnswerConflict as conflict:
            messages.error(request, str(conflict))
            theirs = conflict.answer and conflict.answer.pk
            form = AnswerForm(initial={"text": text, "expected_answer": theirs})
        except services.ServiceError as error:
            messages.error(request, str(error))
    return clarification_page(request, member, clarification_id, form)


@require_POST
@exam_view(examiner_only=True)
def withdraw_answer(request: HtmxRequest, member: Member, clarification_id: int) -> HttpResponse:
    clarification = get_clarification(member.exam, clarification_id)
    expected = request.POST.get("expected_answer", "")
    with flash_errors(request):
        services.withdraw_answer(
            clarification, expected_answer=int(expected) if expected.isdigit() else 0
        )
        messages.success(request, "Answer withdrawn.")
    return redirect("examiner:clarification", member.exam.pk, clarification_id)


@require_POST
@exam_view(examiner_only=True)
def announce_clarification(
    request: HtmxRequest, member: Member, clarification_id: int
) -> HttpResponse:
    form = TextForm(request.POST)
    if form.is_valid():
        clarification = get_clarification(member.exam, clarification_id)
        with flash_errors(request):
            services.announce(member, text=form.cleaned_data["text"], clarification=clarification)
            messages.success(request, f"Announced {clarification.label} to all venues.")
            return redirect("examiner:clarification", member.exam.pk, clarification_id)
    return clarification_page(request, member, clarification_id, AnswerForm(request.POST))


@require_POST
@exam_view(examiner_only=True)
def reopen_clarification(
    request: HtmxRequest, member: Member, clarification_id: int
) -> HttpResponse:
    clarification = get_clarification(member.exam, clarification_id)
    with flash_errors(request):
        services.reopen_clarification(clarification)
        messages.success(request, f"Reopened {clarification.label}.")
    return redirect("examiner:clarification", member.exam.pk, clarification_id)


@require_POST
@exam_view(examiner_only=True)
def merge_clarifications(
    request: HtmxRequest, member: Member, clarification_id: int
) -> HttpResponse:
    source = get_clarification(member.exam, clarification_id)
    target = pick(member.exam.clarifications.all(), request.POST.get("target"))
    if target is None:
        messages.error(request, "Pick a clarification to merge into.")
        return redirect("examiner:clarification", member.exam.pk, clarification_id)
    with flash_errors(request):
        clarification_id = services.merge_clarifications(source=source, target=target).pk
        messages.success(request, "Merged.")
    return redirect("examiner:clarification", member.exam.pk, clarification_id)


@require_POST
@exam_view(examiner_only=True)
def reword_clarification(
    request: HtmxRequest, member: Member, clarification_id: int
) -> HttpResponse:
    clarification = get_clarification(member.exam, clarification_id)
    form = TextForm(request.POST, optional=True)
    if not form.is_valid():
        messages.error(request, form_errors(form))
        return redirect("examiner:clarification", member.exam.pk, clarification_id)
    with flash_errors(request):
        services.reword_clarification(clarification, form.cleaned_data["text"])
        messages.success(request, "Wording saved.")
    return redirect("examiner:clarification", member.exam.pk, clarification_id)


@require_POST
@exam_view(examiner_only=True)
def relabel_clarification(
    request: HtmxRequest, member: Member, clarification_id: int
) -> HttpResponse:
    clarification = get_clarification(member.exam, clarification_id)
    form = LabelForm(request.POST)
    if not form.is_valid():
        return clarification_page(request, member, clarification_id, label_form=form)
    label = form.cleaned_data["label"]
    if label != clarification.label:
        with flash_errors(request):
            services.relabel_clarification(clarification, label)
            messages.success(request, f"Moved from {clarification.label} to {label}.")
    return redirect("examiner:clarification", member.exam.pk, clarification_id)


@require_POST
@exam_view(examiner_only=True)
def move_report(request: HtmxRequest, member: Member, report_id: int) -> HttpResponse:
    report = get_object_or_404(
        Report.objects.of_exam(member.exam).select_related("clarification"),
        pk=report_id,
    )
    choice = request.POST.get("target")
    target = pick(member.exam.clarifications.all(), choice)
    if target is None and choice != "new":
        messages.error(request, "Pick a clarification to move the seat to.")
        return redirect("examiner:clarification", member.exam.pk, report.clarification_id)
    source_id = report.clarification_id
    with flash_errors(request):
        services.move_report(report, target=target)
        messages.success(request, f"Moved {report.venue} {report.seat}.")
        # Stay on the source unless its last seat just left.
        if Clarification.objects.filter(pk=source_id, closed_reason="").exists():
            return redirect("examiner:clarification", member.exam.pk, source_id)
    return redirect("examiner:clarification", member.exam.pk, report.clarification_id)


def readout_status(exam: Exam) -> list[tuple[Announcement, list[str]]]:
    """Each announcement with the known venues it has not been read out in yet."""
    readouts = Prefetch("readouts", Readout.objects.select_related("member"))
    announcements = list(exam.announcements.select_related("member").prefetch_related(readouts))
    venues = set(Report.objects.of_exam(exam).values_list("venue", flat=True))
    venues |= {r.venue for a in announcements for r in a.readouts.all()}
    return [(a, sorted(venues - {r.venue for r in a.readouts.all()})) for a in announcements]


@require_GET
@exam_view(examiner_only=True)
@polled
def announcements(request: HtmxRequest, member: Member) -> HttpResponse:
    context = {"announcements": readout_status(member.exam), **suggestions(member.exam)}
    return render_live(request, member, "examdesk/examiner/announcements.html", context)


@require_GET
@exam_view(examiner_only=True)
@polled
def announcements_badge(request: HtmxRequest, member: Member) -> HttpResponse:
    """Announcements not yet read out in every venue."""
    missing = sum(1 for _, venues in readout_status(member.exam) if venues)
    return nav_badge(request, member, "announcements_badge", {"missing": missing})


@require_POST
@exam_view(examiner_only=True)
def announce(request: HtmxRequest, member: Member) -> HttpResponse:
    form = TextForm(request.POST)
    if not form.is_valid():
        messages.error(request, form_errors(form))
    else:
        with flash_errors(request):
            services.announce(member, text=form.cleaned_data["text"])
            messages.success(request, "Announced to all venues.")
    return redirect("examiner:announcements", member.exam.pk)


@require_POST
@exam_view(examiner_only=True)
def delete_announcement(request: HtmxRequest, member: Member, announcement_id: int) -> HttpResponse:
    announcement = get_object_or_404(Announcement, pk=announcement_id, exam=member.exam)
    with flash_errors(request):
        services.delete_announcement(announcement)
        messages.success(request, "Deleted the announcement.")
    return redirect("examiner:announcements", member.exam.pk)


@dataclass(frozen=True)
class SeatTally:
    seat: str
    tally: list[tuple[Report.State, int]]  # in `Report.PROGRESS` order, without zeros

    @property
    def least(self) -> Report.State:
        """The state of the seat's least advanced question."""
        return self.tally[0][0]


@require_GET
@exam_view(examiner_only=True)
@polled
def seats(request: HtmxRequest, member: Member) -> HttpResponse:
    """Seat tiles by venue with a count per state. Read out counts as delivered."""
    counts: defaultdict[tuple[str, str], Counter[Report.State]] = defaultdict(Counter)
    for r in Report.objects.of_exam(member.exam).with_state():
        state = Report.State.DELIVERED if r.state == Report.State.READ_OUT else r.state
        counts[r.venue, r.seat][state] += 1
    tiles = []
    for venue, seat in sorted(counts, key=lambda place: natural(place[1])):
        tally = [(s, counts[venue, seat][s]) for s in Report.PROGRESS if counts[venue, seat][s]]
        tiles.append((venue, SeatTally(seat, tally)))
    venues = group_by_venue(tiles)
    return render_live(request, member, "examdesk/examiner/seats.html", {"venues": venues})


@require_GET
@exam_view(examiner_only=True)
@polled
def seat(request: HtmxRequest, member: Member) -> HttpResponse:
    """A seat's reports with their delivery history."""
    venue, seat = seat_from(request)
    reports = (
        Report.objects.at_seat(member.exam, venue, seat)
        .with_state()
        .with_deliveries()
        .select_related("member")
    )
    context = {"venue": venue, "seat": seat, "reports": reports}
    return render_live(request, member, "examdesk/examiner/seat.html", context)


@require_GET
@exam_view(examiner_only=True)
def members(request: HtmxRequest, member: Member) -> HttpResponse:
    """Members grouped by venue, online first.

    Not `polled`: online status changes without the exam version changing.
    """
    people = sorted(member.exam.members.active(), key=lambda m: (not m.online, m.name.casefold()))
    venues = group_by_venue((person.venue or "", person) for person in people)
    template = "examdesk/examiner/members.html"
    return render_live(request, member, template, {"venues": venues})


@require_POST
@exam_view(examiner_only=True)
def remove_member(request: HtmxRequest, member: Member, member_id: int) -> HttpResponse:
    removed = get_object_or_404(member.exam.members.active().exclude(pk=member.pk), pk=member_id)
    services.remove_member(removed)
    messages.success(request, f"Removed {removed.name}.")
    return redirect("examiner:members", member.exam.pk)


@require_GET
@exam_view(examiner_only=True)
def exam_settings(request: HtmxRequest, member: Member) -> HttpResponse:
    return settings_page(request, member)


def settings_page(
    request: HtmxRequest, member: Member, delete_form: DeleteExamForm | None = None
) -> HttpResponse:
    """A bound `delete_form` reopens the delete dialog with its errors."""
    links = []
    for role in Role:
        url = request.build_absolute_uri(reverse("join", args=[member.exam.token(role)]))
        qr = mark_safe(segno.make(url, error="m").svg_inline(scale=5, omitsize=True))
        links.append({"role": role, "url": url, "qr": qr})
    delete_form = delete_form or DeleteExamForm(exam_name=member.exam.name)
    context = {"member": member, "links": links, "delete_form": delete_form}
    template = "examdesk/examiner/settings.html"
    return render(request, template, context, status=400 if delete_form.errors else 200)


@require_POST
@exam_view(examiner_only=True)
def reset_link(request: HtmxRequest, member: Member) -> HttpResponse:
    if (role := request.POST.get("role")) not in Role.values:
        return HttpResponse("Unknown role.", status=400)
    services.reset_link(member.exam, Role(role))
    messages.success(request, f"Reset the {Role(role).label} link.")
    return redirect("examiner:settings", member.exam.pk)


@require_POST
@exam_view(examiner_only=True)
def close_exam(request: HtmxRequest, member: Member) -> HttpResponse:
    services.close_exam(member.exam)
    messages.success(request, "Closed the exam.")
    return redirect("examiner:settings", member.exam.pk)


@require_POST
@exam_view(examiner_only=True)
def reopen_exam(request: HtmxRequest, member: Member) -> HttpResponse:
    services.reopen_exam(member.exam)
    messages.success(request, "Reopened the exam.")
    return redirect("examiner:settings", member.exam.pk)


@require_POST
@exam_view(examiner_only=True)
def delete_exam(request: HtmxRequest, member: Member) -> HttpResponse:
    form = DeleteExamForm(request.POST, exam_name=member.exam.name)
    if not form.is_valid():
        return settings_page(request, member, form)
    with flash_errors(request):
        services.delete_exam(member.exam)
        messages.success(request, f"Deleted {member.exam.name}.")
        return redirect("home")
    return redirect("examiner:settings", member.exam.pk)
