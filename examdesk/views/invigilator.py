"""The invigilator screens (phone), also open to examiners."""

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.db.models import Prefetch, QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .. import services, session
from ..access import HtmxRequest, exam_view
from ..forms import ReportForm, ReportStartForm, VenueForm
from ..labels import natural, top_level
from ..matching import rank_similar, same_question, similar_enough
from ..models import (
    Announcement,
    Answer,
    Clarification,
    Delivery,
    Exam,
    Member,
    Readout,
    Report,
    SeatClaim,
)
from ..seats import seat_from, seat_url
from .common import (
    back_to_report,
    flash_errors,
    get_clarification,
    group_by_venue,
    pick,
    redirect_next,
    suggestions,
)
from .live import polled, render_live


def to_read_out(member: Member) -> QuerySet[Announcement]:
    """Not yet read out in the member's venue; all of them if none is set."""
    announcements = member.exam.announcements.all()
    return announcements.exclude(readouts__venue=member.venue) if member.venue else announcements


def invigilator_page(
    request: HtmxRequest, member: Member, page: str, context: dict[str, Any], tab: str = ""
) -> HttpResponse:
    """Render `invigilator/<page>.html` with the tab `tab` (by default `page`) active.

    Asks for a venue once per exam, and releases the member's seat claim on any other
    page than `deliver_seat`.
    """
    exam = member.exam
    if page != "deliver_seat" and "v" not in request.GET:
        services.release_seat(member)
    ask_venue = member.venue is None and not request.htmx
    if ask_venue:
        services.set_venue(member, "")
    context |= {
        "tab": tab or page,
        "invigilator_screen": True,
        "ask_venue": ask_venue,
        **suggestions(exam),
    }
    return render_live(request, member, f"examdesk/invigilator/{page}.html", context)


def deliver_seat_url(member: Member, report: Report, back: str = "") -> str:
    """`back="report"` points the back button at Report instead of To deliver."""
    return seat_url(
        "invigilator:deliver_seat",
        member.exam.pk,
        report.venue,
        report.seat,
        report=report.pk,
        back=back,
    )


@require_GET
@exam_view(live_only=True)
@polled
def tab_bar(request: HtmxRequest, member: Member) -> HttpResponse:
    """Tab badges and the newest unsnoozed announcement to read out; buttons return to `here`."""
    tab = request.GET.get("tab")
    announcements = list(to_read_out(member))
    later = session.snoozed(request.session, member.exam)
    due = [a for a in announcements if later.get(a.pk, 0) <= time.time()]
    alert = due[0] if due and tab != "announcements" else None
    counts = {
        "open_clarifications": Report.objects.open().in_venue_of(member).count(),
        # Seats, as the To deliver tab lists them, not their questions.
        "to_deliver": Report.objects.to_deliver()
        .in_venue_of(member)
        .values("venue", "seat")
        .distinct()
        .count(),
        "announcements": len(announcements),
    }
    context = {
        "tab": tab,
        "counts": counts,
        "alert": alert,
        # Changes when a put-off alert returns, so that it vibrates again.
        "alert_key": alert and f"{alert.pk}.{int(later.get(alert.pk, 0))}",
        "here": request.GET.get("here", ""),
    }
    return render_live(
        request, member, "examdesk/invigilator/base.html", context, partial="tab_bar"
    )


@dataclass(frozen=True)
class Match:
    clarification: Clarification
    asked_here: Report | None  # the seat's report on it
    similarity: int | None  # to the typed text, in percent


def find_matches(
    exam: Exam, venue: str, seat: str, label: str, text: str = ""
) -> tuple[list[Match], list[Match]]:
    """Unclosed clarifications under the label's top-level question, and similar ones under
    other questions, most similar to `text` first."""
    asked = {r.clarification_id: r for r in Report.objects.at_seat(exam, venue, seat)}
    unclosed = (
        exam.clarifications.filter(closed_reason="")
        .with_photo()
        .select_related("current_answer")
        .prefetch_related("reports")
    )
    typed = bool(text.strip())
    here, elsewhere = [], []
    for c, score in rank_similar([text], unclosed):
        match = Match(c, asked.get(c.pk), round(score * 100) if typed else None)
        if same_question(label, c.label):
            here.append(match)
        elif similar_enough(score):
            elsewhere.append(match)
    return here, elsewhere


@exam_view(live_only=True)
def report(request: HtmxRequest, member: Member) -> HttpResponse:
    """Step 1: seat and question. Step 2: join a match or create a clarification."""
    exam = member.exam
    if request.method == "POST":
        form = ReportForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            clarification = (
                get_clarification(exam, data["clarification"]) if data["clarification"] else None
            )
            with flash_errors(request):
                saved = services.add_report(
                    member,
                    client_key=data["client_key"],
                    venue=data["venue"],
                    seat=data["seat"],
                    label=data["label"],
                    text=data["text"],
                    photo=data["photo"],
                    clarification=clarification,
                )
                return redirect("invigilator:report_saved", exam.pk, saved.pk)
        start_form = ReportStartForm(request.POST)
        context: dict[str, Any] = {
            "report_form": form,
            "client_key": request.POST.get("client_key"),
        }
    else:
        # Step 1 submits a label; without one, prefill the last report's seat and question.
        mine = Report.objects.filter(member=member).select_related("clarification")
        last = (mine.filter(venue=member.venue) if member.venue else mine).last()
        start_form = ReportStartForm(
            request.GET if "label" in request.GET else None,
            initial={
                "venue": request.GET.get("venue") or (last and last.venue) or member.venue,
                "seat": request.GET.get("seat") or (last and last.seat),
                "label": request.GET.get("label") or (last and last.clarification.label),
            },
        )
        if not start_form.is_bound and start_form.initial["seat"]:
            del start_form.fields["seat"].widget.attrs["autofocus"]
            start_form.fields["label"].widget.attrs["autofocus"] = True
        context = {"client_key": uuid.uuid4()}
    if start_form.is_valid() and "edit" not in request.GET:
        text = (request.POST or request.GET).get("text", "")
        seat = start_form.cleaned_data
        here, elsewhere = find_matches(exam, seat["venue"], seat["seat"], seat["label"], text)
        groups = (
            [(f"Under {top_level(seat['label'])}", here)]
            if text.strip()
            else [
                ("Answered", [m for m in here if m.clarification.current_answer_id]),
                (
                    "Open",
                    [m for m in here if not m.clarification.current_answer_id],
                ),
            ]
        )
        groups.append(("Under another question", elsewhere))
        context |= {"matching": True, "text": text, "any_match": bool(here), "groups": groups}
        if request.htmx:  # typing in the question re-ranks the matches
            context |= {"member": member, "start_form": start_form}
            return render(request, "examdesk/invigilator/report.html#matches", context)
    return invigilator_page(request, member, "report", context | {"start_form": start_form})


@require_GET
@exam_view(live_only=True)
@polled
def report_saved(request: HtmxRequest, member: Member, report_id: int) -> HttpResponse:
    """What a report just saved, to check before reporting the next."""
    reports = Report.objects.of_exam(member.exam).with_state()
    report = get_object_or_404(reports, pk=report_id)
    return invigilator_page(request, member, "report_saved", {"report": report}, tab="report")


@require_GET
@exam_view(live_only=True)
@polled
def open_clarifications(request: HtmxRequest, member: Member) -> HttpResponse:
    """Open reports in the member's venue, grouped by label, oldest first."""
    reports = Report.objects.open().in_venue_of(member).select_related("clarification")
    clarifications: defaultdict[Clarification, list[Report]] = defaultdict(list)
    for report in reports.order_by("clarification__created_at", "clarification", "created_at"):
        clarifications[report.clarification].append(report)
    groups: defaultdict[str, list[tuple[Clarification, list[Report]]]] = defaultdict(list)
    for clarification, reports_of_it in clarifications.items():
        groups[clarification.label].append((clarification, reports_of_it))
    ordered = sorted(groups.items(), key=lambda item: natural(item[0]))
    return invigilator_page(request, member, "open_clarifications", {"groups": ordered})


@dataclass(frozen=True)
class SeatTile:
    venue: str
    seat: str
    to_deliver: int
    claim: SeatClaim | None = None  # someone else delivering to it


@require_GET
@exam_view(live_only=True)
@polled
def to_deliver(request: HtmxRequest, member: Member) -> HttpResponse:
    """Seats still to deliver in seat order, then fully delivered seats, newest first."""
    seats: defaultdict[tuple[str, str], list[Report]] = defaultdict(list)
    for report in Report.objects.answered().with_state().in_venue_of(member):
        seats[report.venue, report.seat].append(report)
    claims = {
        (claim.venue, claim.seat): claim for claim in member.exam.claims.select_related("member")
    }
    undelivered_tiles: list[SeatTile] = []
    delivered: list[tuple[datetime, SeatTile]] = []
    for (venue, seat), reports in seats.items():
        if undelivered := [report for report in reports if not report.delivered]:
            claim = claims.get((venue, seat))
            claim = claim if claim and claim.holds(undelivered) else None
            undelivered_tiles.append(SeatTile(venue, seat, len(undelivered), claim))
        else:
            last = max(report.delivered_at for report in reports if report.delivered_at)
            delivered.append((last, SeatTile(venue, seat, 0)))
    undelivered_tiles.sort(key=lambda tile: (natural(tile.venue), natural(tile.seat)))
    delivered.sort(key=lambda pair: pair[0], reverse=True)
    context = {
        "undelivered": group_by_venue((tile.venue, tile) for tile in undelivered_tiles),
        "delivered": group_by_venue((tile.venue, tile) for _, tile in delivered),
        "delivered_count": len(delivered),
    }
    return invigilator_page(request, member, "to_deliver", context)


@require_GET
@exam_view(live_only=True)
@polled
def deliver_seat(request: HtmxRequest, member: Member) -> HttpResponse:
    """Opens on `report`, else the first undelivered one. Opening it claims the seat."""
    venue, seat = seat_from(request)
    reports = list(
        Report.objects.at_seat(member.exam, venue, seat).answered().with_state().with_deliveries()
    )
    taken_from = None
    if (undelivered := [r for r in reports if not r.delivered]) and "v" not in request.GET:
        claim = member.exam.claims.select_related("member").filter(venue=venue, seat=seat).first()
        if claim and claim.member_id != member.pk and claim.holds(undelivered):
            taken_from = claim
        services.claim_seat(member, venue=venue, seat=seat)
    wanted = request.GET.get("report")
    current = next((r for r in reports if str(r.pk) == wanted), None) or next(
        iter(undelivered), reports[0] if reports else None
    )
    context = {
        "venue": venue,
        "seat": seat,
        "reports": reports,
        "current": current,
        "back": back_to_report(request),
        "taken_from": taken_from,
    }
    return invigilator_page(request, member, "deliver_seat", context, tab="to_deliver")


@require_POST
@exam_view(live_only=True)
def mark_delivered(request: HtmxRequest, member: Member, report_id: int) -> HttpResponse:
    report = get_object_or_404(Report.objects.of_exam(member.exam), pk=report_id)
    answer = pick(
        Answer.objects.filter(clarification=report.clarification_id), request.POST.get("answer")
    )
    back = back_to_report(request)
    with flash_errors(request):
        services.mark_delivered(report, answer=answer, member=member)
        label = report.clarification.label
        messages.success(request, f"Delivered {label} to {report.venue} {report.seat}.")
    return redirect(deliver_seat_url(member, report, back))


@require_POST
@exam_view(live_only=True)
def unmark_delivered(request: HtmxRequest, member: Member, delivery_id: int) -> HttpResponse:
    deliveries = Delivery.objects.filter(report__clarification__exam=member.exam)
    delivery = get_object_or_404(deliveries.select_related("report__clarification"), pk=delivery_id)
    report = delivery.report
    back = back_to_report(request)
    with flash_errors(request):
        services.unmark_delivered(delivery)
        messages.success(request, f"Unmarked {report.venue} {report.seat}.")
    return redirect(deliver_seat_url(member, report, back))


@require_GET
@exam_view(live_only=True)
@polled
def announcements(request: HtmxRequest, member: Member) -> HttpResponse:
    """Each announcement with its readout in the member's venue, if any."""
    found = member.exam.announcements.prefetch_related(
        Prefetch("readouts", Readout.objects.select_related("member"))
    )
    items = [
        (
            announcement,
            next((r for r in announcement.readouts.all() if r.venue == member.venue), None),
        )
        for announcement in found
    ]
    return invigilator_page(request, member, "announcements", {"items": items})


@require_POST
@exam_view(live_only=True)
def choose_venue(request: HtmxRequest, member: Member) -> HttpResponse:
    """Set "My venue": typed, picked from the known venues (`pick`), or none (`all`)."""
    form = VenueForm({"venue": request.POST.get("pick") or request.POST.get("venue", "")})
    if "all" in request.POST:
        services.set_venue(member, "")
    elif form.is_valid():
        services.set_venue(member, form.cleaned_data["venue"])
    return redirect_next(request, reverse("invigilator:report", args=[member.exam.pk]))


@require_POST
@exam_view(live_only=True)
def mark_read_out(request: HtmxRequest, member: Member, announcement_id: int) -> HttpResponse:
    """A member without a venue posts one, which then becomes theirs."""
    announcement = get_object_or_404(Announcement, pk=announcement_id, exam=member.exam)
    form = VenueForm(request.POST)
    venue = member.venue or (form.cleaned_data["venue"] if form.is_valid() else "")
    back = reverse("invigilator:announcements", args=[member.exam.pk])
    if not venue:
        messages.error(request, "Pick the venue where it was read out.")
        return redirect(back)
    with flash_errors(request):
        services.mark_read_out(announcement, venue=venue, member=member)
        services.set_venue(member, venue)
        messages.success(request, f"Marked as read out in {venue}.")
    return redirect_next(request, back)


@require_POST
@exam_view(live_only=True)
def snooze_announcement(request: HtmxRequest, member: Member, announcement_id: int) -> HttpResponse:
    announcement = get_object_or_404(Announcement, pk=announcement_id, exam=member.exam)
    until = time.time() + settings.EXAMDESK_SNOOZE_FOR.total_seconds()
    session.snooze(request.session, member.exam, announcement.pk, until)
    return redirect_next(request, reverse("invigilator:announcements", args=[member.exam.pk]))


@require_POST
@exam_view(live_only=True)
def unmark_read_out(request: HtmxRequest, member: Member, announcement_id: int) -> HttpResponse:
    readout = get_object_or_404(
        Readout.objects.select_related("announcement"),
        announcement_id=announcement_id,
        announcement__exam=member.exam,
        venue=member.venue,
    )
    with flash_errors(request):
        services.unmark_read_out(readout)
        messages.success(request, f"Unmarked in {readout.venue}.")
    return redirect("invigilator:announcements", member.exam.pk)
