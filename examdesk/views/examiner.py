"""The examiner's screens (laptop)."""

import asyncio
import logging
import mimetypes
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import openai
import segno
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import connections
from django.db.models import Count, Prefetch, QuerySet, prefetch_related_objects
from django.forms.models import model_to_dict
from django.http import Http404, HttpResponse, HttpResponseBase, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_POST
from pydantic import ValidationError

from .. import ai, export, pdf, services, session
from ..access import HtmxRequest, exam_view
from ..forms import (
    AIConnectForm,
    AIModelsForm,
    AnswerForm,
    ClarificationFilterForm,
    DeleteExamForm,
    ExamFileForm,
    ExamSettingsForm,
    LabelForm,
    TextForm,
    form_errors,
)
from ..labels import (
    covers,
    natural,
    normalize_place,
    parse_question_numbers,
    place_order,
    top_level,
)
from ..matching import percent, rank_similar, same_question, similar_enough
from ..models import (
    SEAT_STATES,
    Analysis,
    Announcement,
    Clarification,
    Exam,
    ExamFile,
    FilePage,
    Member,
    Report,
    Role,
    SeatState,
)
from ..seats import seat_from
from .common import (
    ai_view,
    flash_errors,
    get_clarification,
    group_by_venue,
    pick,
    readout_status,
    redirect_next,
    suggestions,
)
from .live import is_refresh, render_live

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClarificationRow:
    clarification: Clarification
    reports: list[Report]
    unresolved: int
    waiting_since: datetime | None


# Wait is measured from the longest-waiting seat, so new reports don't bump a clarification.
SORT_KEYS: dict[str, Callable[[ClarificationRow], Any]] = {
    "question": lambda row: natural(row.clarification.label),
    "seats": lambda row: -row.unresolved,
    "wait": lambda row: (
        row.waiting_since is None,
        row.waiting_since or row.clarification.created_at,
    ),
}


def waiting_since(reports: list[Report]) -> datetime | None:
    """When the longest-waiting seat asked."""
    return min((r.created_at for r in reports if r.state in Report.UNRESOLVED), default=None)


def queue(request: HtmxRequest, exam: Exam, query: QueryDict) -> dict[str, Any]:
    """The clarifications that `query` filters and sorts, a page of them, and the state tabs.

    Saves `query` as this device's filters.
    """
    used = set(exam.clarifications.values_list("label", flat=True))
    # Offer top-level labels too: Q3 selects Q3a and Q3b.
    labels = {label for used_label in used for label in (used_label, top_level(used_label))}
    # A saved filter may name a label that was since relabeled or deleted.
    query = query.copy()
    query.setlist("label", [label for label in query.getlist("label") if label in labels])
    if not is_refresh(request):
        session.save_filters(request.session, exam, query.urlencode())
    form = ClarificationFilterForm(query, labels=sorted(labels, key=natural))
    filters = form.cleaned_data if form.is_valid() else {}
    state = filters.get("state") or Clarification.State.OPEN
    found = exam.clarifications.all()
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
    counts: Counter[str] = Counter()
    for clarification in found:
        tab = "closed" if clarification.closed_reason else clarification.display_state
        counts[tab] += 1
        if state == tab:
            reports = list(clarification.reports.all())
            unresolved = sum(r.state in Report.UNRESOLVED for r in reports)
            rows.append(
                ClarificationRow(clarification, reports, unresolved, waiting_since(reports))
            )
    sort = filters.get("sort") or "wait"
    rows.sort(key=SORT_KEYS["wait"])
    rows.sort(key=SORT_KEYS[sort.removeprefix("-")], reverse=sort.startswith("-"))  # ties by wait
    return {
        "form": form,
        "query": query,
        "rows": rows,
        "page_obj": Paginator(rows, settings.EXAMDESK_PAGE_SIZE).get_page(query.get("page")),
        "state": state,
        "tabs": [
            (name, title, counts[name]) for name, title in ClarificationFilterForm.STATES.items()
        ],
        "filtered": bool(filters.get("label")),
    }


def saved_filters_redirect(request: HtmxRequest, exam: Exam) -> HttpResponse | None:
    """A redirect to this device's saved filters for a page opened without a query string, except
    on Back or a refresh."""
    saved = session.filters(request.session, exam)
    if request.GET or not saved or request.htmx.history_restore_request or is_refresh(request):
        return None
    return redirect(f"{request.path}?{saved}")


@require_GET
@exam_view(examiner_only=True)
def clarifications(request: HtmxRequest, member: Member) -> HttpResponse:
    exam = member.exam
    if response := saved_filters_redirect(request, exam):
        return response
    context = queue(request, exam, request.GET)
    no_open_clarifications = (
        not context["rows"]
        and not context["filtered"]
        and context["state"] == Clarification.State.OPEN
    )
    invigilators = exam.members.active().filter(role=Role.INVIGILATOR)
    if no_open_clarifications and not invigilators.exists():
        context["invite_link"] = join_link(request, exam, Role.INVIGILATOR)
    template = "examdesk/examiner/clarifications.html"
    return render_live(request, member, template, context | suggestions(exam))


@require_GET
@exam_view(examiner_only=True)
def export_clarifications(request: HtmxRequest, member: Member) -> HttpResponse:
    filename = f"{member.exam.name} clarifications.csv"
    content_disposition = content_disposition_header(as_attachment=True, filename=filename)
    return HttpResponse(
        export.clarifications_csv(member.exam),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition},
    )


def clarification_page(
    request: HtmxRequest,
    member: Member,
    clarification_id: int,
    form: AnswerForm | None = None,
    label_form: LabelForm | None = None,
) -> HttpResponse:
    """The clarification beside the queue. The query filters the queue, or else this device's
    last filters do. A bound `label_form` reopens the relabel dialog with its errors."""
    exam = member.exam
    clarification = get_clarification(exam, clarification_id)
    correcting = form is not None
    if form is None:
        current = clarification.current_answer
        form = AnswerForm(
            initial={
                "text": current.text if current else "",
                "expected_answer": current and current.pk,
            }
        )
    query = request.GET or QueryDict(session.filters(request.session, exam))
    context = queue(request, exam, query)
    queued = [row.clarification for row in context["rows"]]
    after = queued.index(clarification) + 1 if clarification in queued else 0
    prefetch_related_objects(
        [clarification], Prefetch("reports", Report.objects.with_state().select_related("member"))
    )
    reports = list(clarification.reports.all())
    ranked = rank_similar(clarification.texts, unmerged_others(exam, clarification))
    current = clarification.current_answer
    context |= {
        "clarification": clarification,
        "can_answer": exam.is_live and not clarification.closed_reason,
        "next_in_queue": next((c for c in queued[after:] + queued if c != clarification), None),
        "waiting_since": waiting_since(reports),
        # without a wording, a photo stands for the question
        "question_photo": None
        if clarification.wording
        else next((r for r in reports if r.photo), None),
        # with a fast model, replaced by `clarification_duplicates`
        "duplicates": possible_duplicates(clarification, ranked),
        "answer_form": form,
        "reports": reports,
        "answers": clarification.answers.select_related("member"),
        "withdrawable": current and not current.deliveries.exists(),
        "others": [other for other, _ in ranked if not other.closed_reason],
        "canned": settings.EXAMDESK_CANNED_ANSWERS,
        "label_form": label_form or LabelForm(initial={"label": clarification.label}, exam=exam),
        "correcting": correcting,
        **suggestions(exam),
    }
    path = reverse("examiner:clarification", args=[exam.pk, clarification_id])
    template = "examdesk/examiner/clarification.html"
    response = render_live(request, member, template, context, path=path)
    response.status_code = 400 if label_form and label_form.errors else 200
    return response


DUPLICATE_CANDIDATES = 10


def unmerged_others(exam: Exam, clarification: Clarification) -> QuerySet[Clarification]:
    return (
        exam.clarifications.exclude(pk=clarification.pk)
        .exclude(closed_reason=Clarification.ClosedReason.MERGED)
        .prefetch_related(Prefetch("reports", Report.objects.with_state()))
    )


def possible_duplicates(
    clarification: Clarification, ranked: list[tuple[Clarification, float]]
) -> list[tuple[Clarification, int]]:
    """Those under the same question or similar enough, with the similarity in percent, except
    any that rounds to 0%."""
    return [
        (other, similarity_percent)
        for other, score in ranked
        if (similarity_percent := percent(score)) > 0
        and (same_question(clarification.label, other.label) or similar_enough(score))
    ]


def rank_others(
    member: Member, clarification_id: int
) -> tuple[Clarification, list[tuple[Clarification, float]]]:
    """The clarification, and the unmerged others by similarity to it."""
    clarification = get_clarification(member.exam, clarification_id)
    prefetch_related_objects([clarification], "reports")
    return clarification, rank_similar(
        clarification.texts, unmerged_others(member.exam, clarification)
    )


@require_GET
@ai_view("ai_fast_model", examiner_only=True, live_only=True)
async def clarification_duplicates(
    request: HtmxRequest, member: Member, clarification_id: int
) -> HttpResponseBase:
    """Of the most alike clarifications, those the AI service finds ask the same thing, likeliest
    first. Without its answer, the most alike."""
    clarification, ranked = await sync_to_async(rank_others)(member, clarification_id)
    candidates = sorted(
        (other for other, score in ranked[:DUPLICATE_CANDIDATES] if percent(score) > 0),
        key=lambda other: other.pk,
    )
    key = ai.duplicates_cache_key(member.exam, clarification, candidates)
    probabilities: list[float] | None = await cache.aget(key) if candidates else []
    if probabilities is None:
        await sync_to_async(connections.close_all)()
        try:
            probabilities = await ai.confirm_duplicates(member.exam, clarification, candidates)
        except (openai.APIError, ValueError) as error:
            logger.warning("Couldn't confirm duplicates: %s", error)
        else:
            await cache.aset(key, probabilities, timeout=None)
    if probabilities is None:
        duplicates = possible_duplicates(clarification, ranked)
    else:
        duplicates = [
            (other, round(probability * 100))
            for probability, other in sorted(
                zip(probabilities, candidates, strict=True), key=lambda pair: pair[0], reverse=True
            )
            if probability >= 0.5
        ]
    context = {
        "member": member,
        "clarification": clarification,
        "duplicates": duplicates,
        "confirmed_by_ai": probabilities is not None,
    }
    template = "examdesk/examiner/clarification.html#duplicates"
    return await sync_to_async(render)(request, template, context)


@require_GET
@exam_view(examiner_only=True)
def clarification(request: HtmxRequest, member: Member, clarification_id: int) -> HttpResponse:
    return saved_filters_redirect(request, member.exam) or clarification_page(
        request, member, clarification_id
    )


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


def to_draft(member: Member, clarification_id: int) -> tuple[Clarification, list[ExamFile]]:
    clarifications = member.exam.clarifications.prefetch_related("reports")
    files = member.exam.files.prefetch_related("pages")
    return get_object_or_404(clarifications, pk=clarification_id), list(files)


@require_POST
@ai_view("ai_careful_model", examiner_only=True, live_only=True)
async def draft_answer(
    request: HtmxRequest, member: Member, clarification_id: int
) -> HttpResponseBase:
    """A drafted answer, as plain text."""
    clarification, files = await sync_to_async(to_draft)(member, clarification_id)
    await sync_to_async(connections.close_all)()
    text = await ai.draft_answer(member.exam, clarification, files)
    return HttpResponse(text, content_type="text/plain")


def load_report_photo(member: Member, report_id: int) -> tuple[bytes, str]:
    reports = Report.objects.of_exam(member.exam).exclude(photo="")
    photo = get_object_or_404(reports, pk=report_id).photo
    content_type, _ = mimetypes.guess_type(photo.url)
    with photo.open("rb"):
        return photo.read(), content_type or "application/octet-stream"


@require_POST
@ai_view("ai_vision_model", examiner_only=True, live_only=True)
async def transcribe_photo(
    request: HtmxRequest, member: Member, report_id: int
) -> HttpResponseBase:
    """The handwriting in a report's photo, as plain text."""
    photo, content_type = await sync_to_async(load_report_photo)(member, report_id)
    await sync_to_async(connections.close_all)()
    text = await ai.transcribe(member.exam, photo, content_type)
    return HttpResponse(text, content_type="text/plain")


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
def merge_clarifications(request: HtmxRequest, member: Member) -> HttpResponse:
    """Merge the `source` clarifications into the `target` one. Shows the target, or `next` on
    failure."""
    clarifications = member.exam.clarifications.all()
    back = reverse("examiner:clarifications", args=[member.exam.pk])
    target = pick(clarifications, request.POST.get("target"))
    if target is None:
        messages.error(request, "Pick a clarification to merge into.")
        return redirect_next(request, back)
    source_ids = [pk for pk in request.POST.getlist("source") if pk.isdigit()]
    sources = list(clarifications.filter(pk__in=source_ids))
    with flash_errors(request):
        services.merge_clarifications(sources=sources, target=target)
        messages.success(request, f"Merged into {target.label}.")
        return redirect("examiner:clarification", member.exam.pk, target.pk)
    return redirect_next(request, back)


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
    form = LabelForm(request.POST, exam=member.exam)
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


@require_GET
@exam_view(examiner_only=True)
def announcements(request: HtmxRequest, member: Member) -> HttpResponse:
    paginator = Paginator(readout_status(member.exam), settings.EXAMDESK_PAGE_SIZE)
    context = {"page_obj": paginator.get_page(request.GET.get("page")), **suggestions(member.exam)}
    return render_live(request, member, "examdesk/examiner/announcements.html", context)


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


@require_GET
@exam_view(examiner_only=True)
def analysis(request: HtmxRequest, member: Member) -> HttpResponse:
    """Seats and clarifications per question, most asked first, beside the AI's findings."""
    exam = member.exam
    if not exam.ai_careful_model:
        raise Http404
    questions = sorted(
        exam.clarifications.exclude(closed_reason=Clarification.ClosedReason.MERGED)
        .values("label")
        .annotate(clarification_count=Count("pk", distinct=True), seat_count=Count("reports")),
        key=lambda question: (
            -question["seat_count"],
            -question["clarification_count"],
            natural(question["label"]),
        ),
    )
    context = {"questions": questions, "analysis": Analysis.objects.filter(exam=exam).first()}
    return render_live(request, member, "examdesk/examiner/analysis.html", context)


def to_analyse(exam: Exam) -> tuple[list[Clarification], list[Announcement], list[ExamFile]]:
    clarifications = exam.clarifications.exclude(closed_reason=Clarification.ClosedReason.MERGED)
    return (
        list(clarifications.prefetch_related("reports", "answers")),
        list(exam.announcements.order_by("created_at")),
        list(exam.files.prefetch_related("pages")),
    )


# Running analyses. The event loop holds only weak references to tasks.
running_analyses: set[asyncio.Task[str | None]] = set()


@require_POST
@ai_view("ai_careful_model", examiner_only=True)
async def analyse(request: HtmxRequest, member: Member) -> HttpResponseBase:
    """Saves the analysis even if the examiner leaves before it finishes."""
    clarifications, announcements, files = await sync_to_async(to_analyse)(member.exam)
    await sync_to_async(connections.close_all)()
    task = asyncio.create_task(analyse_and_save(member.exam, clarifications, announcements, files))
    running_analyses.add(task)
    task.add_done_callback(running_analyses.discard)
    if error := await asyncio.shield(task):
        messages.error(request, error)
    return redirect("examiner:analysis", member.exam.pk)


async def analyse_and_save(
    exam: Exam,
    clarifications: list[Clarification],
    announcements: list[Announcement],
    files: list[ExamFile],
) -> str | None:
    """The error to show, if the analysis failed."""
    try:
        findings = await ai.analyse(exam, clarifications, announcements, files)
    except openai.APITimeoutError:
        return "The AI service took too long."
    except (openai.APIError, ValidationError) as error:
        logger.warning("Couldn't analyse the clarifications: %s", error)
        return "Couldn't analyse the clarifications."
    await sync_to_async(save_and_close_connections, thread_sensitive=False)(exam, findings)
    return None


def save_and_close_connections(exam: Exam, findings: ai.Findings) -> None:
    """Closes the thread's database connection, which no request closes once the examiner has
    left."""
    try:
        services.save_analysis(exam, findings)
    finally:
        connections.close_all()


@dataclass(frozen=True)
class SeatSummary:
    venue: str
    seat: str
    state: SeatState
    question_count: int


@dataclass(frozen=True)
class VenueSeats:
    name: str
    seats: list[SeatSummary]  # those the state filter keeps
    seat_count: int  # of those the search keeps
    state_counts: list[tuple[SeatState, int]]


def seats_page(
    request: HtmxRequest, member: Member, selected_seat: tuple[str, str] | None = None
) -> HttpResponse:
    """Every seat that asked something, by venue, filtered by `?state=` and `?q=`, and the
    `selected_seat`'s questions beside them."""
    state = request.GET.get("state", "")
    state = state if state in SeatState.values else ""
    seat_query = normalize_place(request.GET.get("q", ""))
    order = list(SeatState)
    seat_states: dict[tuple[str, str], SeatState] = {}
    question_counts: Counter[tuple[str, str]] = Counter()
    for report in (
        Report.objects.of_exam(member.exam).filter(seat__contains=seat_query).with_state()
    ):
        place = (report.venue, report.seat)
        question_counts[place] += 1
        seat_state = SEAT_STATES[report.state]
        seat_states[place] = min(seat_states.get(place, seat_state), seat_state, key=order.index)
    venues = []
    for venue, places in group_by_venue(
        (place[0], place) for place in sorted(seat_states, key=place_order)
    ):
        seats = [
            SeatSummary(*place, seat_states[place], question_counts[place]) for place in places
        ]
        counts = Counter(seat.state for seat in seats)
        venues.append(
            VenueSeats(
                name=venue,
                seats=[seat for seat in seats if state in ("", seat.state)],
                seat_count=len(seats),
                state_counts=[(s, counts[s]) for s in SeatState if counts[s]],
            )
        )
    totals = Counter(seat_states.values())
    filters = {"state": state, "q": seat_query}
    context: dict[str, Any] = {
        "venues": venues,
        "tabs": [
            ("", "All", len(seat_states)),
            *((seat_state.value, seat_state.label, totals[seat_state]) for seat_state in SeatState),
        ],
        "state": state,
        "seat_query": seat_query,
        "filters": filters,
        "seat_list_url": reverse(
            "examiner:seats", args=[member.exam.pk], query={k: v for k, v in filters.items() if v}
        ),
    }
    if selected_seat:
        venue, seat = selected_seat
        context |= {
            "selected_seat": selected_seat,
            "reports": Report.objects.at_seat(member.exam, venue, seat)
            .with_state()
            .with_deliveries()
            .select_related("member"),
        }
    return render_live(request, member, "examdesk/examiner/seats.html", context)


@require_GET
@exam_view(examiner_only=True)
def seats(request: HtmxRequest, member: Member) -> HttpResponse:
    return seats_page(request, member)


@require_GET
@exam_view(examiner_only=True)
def seat(request: HtmxRequest, member: Member) -> HttpResponse:
    """The seats, with a seat's questions and their delivery history beside them."""
    return seats_page(request, member, selected_seat=seat_from(request))


@require_GET
@exam_view(examiner_only=True)
def members(request: HtmxRequest, member: Member) -> HttpResponse:
    """Everyone on the exam, online first, optionally of one `?role=`. `?invite=<role>` opens the
    join links on that role. Joining and presence don't change the exam version, so the list
    refreshes every `EXAMDESK_SEEN_EVERY` too."""
    everyone = list(member.exam.members.active())
    role = request.GET.get("role", "")
    role = role if role in Role.values else ""
    people = [person for person in everyone if not role or person.role == role]
    people.sort(key=lambda m: (not m.online, m.name.casefold()))
    role_counts = Counter(person.role for person in everyone)
    invite_role = request.GET.get("invite", "")
    context = {
        "people": people,
        "role": role,
        "tabs": [
            ("", "Everyone", len(everyone)),
            *((r.value, f"{r.label}s", role_counts[r]) for r in Role),
        ],
        "invite_role": invite_role if invite_role in Role.values else "",
        # lazy: refreshes don't show them
        "join_links": lambda: [join_link(request, member.exam, r) for r in Role],
        "presence_update_in": int(settings.EXAMDESK_SEEN_EVERY.total_seconds()),
    }
    return render_live(request, member, "examdesk/examiner/members.html", context)


def join_link(request: HtmxRequest, exam: Exam, role: Role) -> dict[str, Any]:
    url = request.build_absolute_uri(reverse("join", args=[exam.token(role)]))
    qr = mark_safe(segno.make(url, error="m").svg_inline(scale=5, omitsize=True))
    return {"role": role, "url": url, "qr": qr}


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
    request: HtmxRequest,
    member: Member,
    delete_form: DeleteExamForm | None = None,
    connect_form: AIConnectForm | None = None,
    models_form: AIModelsForm | None = None,
    file_form: ExamFileForm | None = None,
    exam_form: ExamSettingsForm | None = None,
) -> HttpResponse:
    """A bound `delete_form` reopens the delete dialog with its errors."""
    exam = member.exam
    delete_form = delete_form or DeleteExamForm(exam_name=exam.name)
    connect_form = connect_form or AIConnectForm()
    models_form = models_form or AIModelsForm(exam=exam)
    file_form = file_form or ExamFileForm(exam=exam)
    exam_form = exam_form or ExamSettingsForm(
        initial=model_to_dict(exam, fields=ExamSettingsForm.base_fields), exam=exam
    )
    context = {
        "delete_form": delete_form,
        "connect_form": connect_form,
        "models_form": models_form,
        "file_form": file_form,
        "exam_form": exam_form,
        **question_numbers_preview(exam_form["question_numbers"].value() or ""),
        "files": exam.files.prefetch_related("pages"),
    }
    path = reverse("examiner:settings", args=[exam.pk])
    template = "examdesk/examiner/settings.html"
    response = render_live(request, member, template, context, path=path)
    forms = (delete_form, connect_form, models_form, file_form, exam_form)
    response.status_code = 400 if any(form.errors for form in forms) else 200
    return response


def question_numbers_preview(text: str) -> dict[str, Any]:
    try:
        return {"question_labels": parse_question_numbers(text)}
    except ValueError as error:
        return {"question_numbers_error": str(error)}


@require_GET
@exam_view(examiner_only=True)
def preview_question_numbers(request: HtmxRequest, member: Member) -> HttpResponse:
    # `1-10, 11a-` previews as `1-10, 11a` while the range is typed.
    text = request.GET.get("question_numbers", "").rstrip(" ,-\N{EN DASH}")
    context = question_numbers_preview(text)
    return render(request, "examdesk/examiner/settings.html#question_numbers_preview", context)


@require_POST
@exam_view(examiner_only=True)
def edit_exam(request: HtmxRequest, member: Member) -> HttpResponse:
    form = ExamSettingsForm(request.POST, exam=member.exam)
    if not form.is_valid():
        return settings_page(request, member, exam_form=form)
    services.edit_exam(member.exam, **form.cleaned_data)
    messages.success(request, "Saved the exam.")
    return redirect("examiner:settings", member.exam.pk)


@require_POST
@exam_view(examiner_only=True)
def connect_ai_service(request: HtmxRequest, member: Member) -> HttpResponse:
    form = AIConnectForm(request.POST)
    if form.is_valid():
        try:
            models = ai.list_models(**form.cleaned_data)
        except openai.AuthenticationError:
            form.add_error("api_key", "The service refused that key.")
        except openai.APIError:
            form.add_error("base_url", "No API answered at that address.")
        else:
            services.connect_ai_service(member.exam, **form.cleaned_data, models=models)
            warn_if_fast_model_lacks_logprobs(request, member.exam)
            return redirect("examiner:settings", member.exam.pk)
    return settings_page(request, member, connect_form=form)


@require_POST
@exam_view(examiner_only=True)
def disconnect_ai_service(request: HtmxRequest, member: Member) -> HttpResponse:
    services.disconnect_ai_service(member.exam)
    return redirect("examiner:settings", member.exam.pk)


@require_POST
@exam_view(examiner_only=True)
def choose_ai_models(request: HtmxRequest, member: Member) -> HttpResponse:
    form = AIModelsForm(request.POST, exam=member.exam)
    if not form.is_valid():
        return settings_page(request, member, models_form=form)
    services.choose_ai_models(member.exam, **form.cleaned_data)
    messages.success(request, "Saved the models.")
    if "fast_model" in form.changed_data:
        warn_if_fast_model_lacks_logprobs(request, member.exam)
    return redirect("examiner:settings", member.exam.pk)


def warn_if_fast_model_lacks_logprobs(request: HtmxRequest, exam: Exam) -> None:
    if not exam.ai_fast_model:
        return
    try:
        has_logprobs = ai.fast_model_returns_logprobs(exam)
    except openai.APIError as error:
        logger.warning("Couldn't test the fast model: %s", error)
        return
    if not has_logprobs:
        fast_model = exam.ai_fast_model
        messages.warning(request, f"With {fast_model}, every possible duplicate shows 100% match.")


@require_POST
@exam_view(examiner_only=True)
def add_file(request: HtmxRequest, member: Member) -> HttpResponse:
    form = ExamFileForm(request.POST, request.FILES, exam=member.exam)
    if not form.is_valid():
        return settings_page(request, member, file_form=form)
    file = services.add_file(member.exam, form.cleaned_data["file"])
    messages.success(request, f"Added {file.name}.")
    return redirect("examiner:settings", member.exam.pk)


@require_POST
@exam_view(examiner_only=True)
def delete_file(request: HtmxRequest, member: Member, file_id: int) -> HttpResponse:
    file = get_object_or_404(member.exam.files, pk=file_id)
    services.delete_file(file)
    messages.success(request, f"Deleted {file.name}.")
    return redirect("examiner:settings", member.exam.pk)


@require_GET
@exam_view(examiner_only=True)
def exam_file(request: HtmxRequest, member: Member, file_id: int) -> HttpResponse:
    """Each page with the text the AI read from it."""
    file = get_object_or_404(member.exam.files.prefetch_related("pages"), pk=file_id)
    return render_live(request, member, "examdesk/examiner/file.html", {"file": file})


def pdf_page(member: Member, file_id: int, number: int) -> tuple[FilePage, bytes]:
    """The page and its file's PDF."""
    pages = FilePage.objects.filter(file__exam=member.exam).exclude(file__pdf="")
    page = get_object_or_404(pages.select_related("file"), file_id=file_id, number=number)
    with page.file.pdf.open("rb") as document:
        return page, document.read()


@require_GET
@exam_view(examiner_only=True)
def page_image(request: HtmxRequest, member: Member, file_id: int, number: int) -> HttpResponse:
    """A PDF page as the AI sees it."""
    _, document = pdf_page(member, file_id, number)
    response = HttpResponse(pdf.render_page(document, number), content_type="image/png")
    max_age = int(settings.EXAMDESK_PHOTO_CACHE_FOR.total_seconds())
    response["Cache-Control"] = f"private, max-age={max_age}"
    return response


@require_POST
@ai_view("ai_vision_model", examiner_only=True)
async def read_page(
    request: HtmxRequest, member: Member, file_id: int, number: int
) -> HttpResponseBase:
    page, document = await sync_to_async(pdf_page)(member, file_id, number)
    await sync_to_async(connections.close_all)()
    text = await ai.read_page(member.exam, document, number)
    await FilePage.objects.filter(pk=page.pk).aupdate(text=text, is_read=True)
    return HttpResponse(status=204)


@require_POST
@exam_view(examiner_only=True)
def reset_link(request: HtmxRequest, member: Member) -> HttpResponse:
    if (role := request.POST.get("role")) not in Role.values:
        return HttpResponse("Unknown role.", status=400)
    services.reset_link(member.exam, Role(role))
    messages.success(request, f"Reset the {Role(role).label} link.")
    return redirect(reverse("examiner:members", args=[member.exam.pk], query={"invite": role}))


@require_POST
@exam_view(examiner_only=True)
def close_exam(request: HtmxRequest, member: Member) -> HttpResponse:
    services.set_live(member.exam, live=False)
    messages.success(request, "Closed the exam.")
    return redirect("examiner:settings", member.exam.pk)


@require_POST
@exam_view(examiner_only=True)
def reopen_exam(request: HtmxRequest, member: Member) -> HttpResponse:
    services.set_live(member.exam, live=True)
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
