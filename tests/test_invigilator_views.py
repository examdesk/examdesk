"""The invigilator screens."""

import re
import uuid
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pytest_django.fixtures import Settings

from examdesk import services
from examdesk.models import Clarification, Exam, Report, Role
from examdesk.similarity import similarity
from examdesk.templatetags.examdesk_tags import icon
from tests.helpers import (
    HTMX,
    REFRESH,
    fresh,
    member,
    named,
    open_link,
    report,
    seat_page,
    set_question_numbers,
    set_venues,
    url,
    with_venue,
)

pytestmark = pytest.mark.django_db


def test_report_retry_does_not_duplicate(invigilator: Client, exam: Exam) -> None:
    data = {"client_key": uuid.uuid4(), "venue": "hall 1", "seat": " 14", "label": "3(b)"}
    data["text"] = "Is x an integer?"
    locations = {
        invigilator.post(url("invigilator:report", exam), data)["Location"] for _ in range(2)
    }
    r = Report.objects.get()
    assert (r.venue, r.seat, r.clarification.label, r.member.name) == ("HALL1", "14", "Q3b", "Ann")
    assert locations == {url("invigilator:report_saved", exam, r.pk)}


def test_cannot_join_clarification_of_other_exam(invigilator: Client, exam: Exam) -> None:
    other = services.create_exam(name="Other", owner=exam.owner, time_zone=ZoneInfo("UTC"))
    data = {"client_key": uuid.uuid4(), "venue": "HALL1", "seat": "14", "label": "Q3b"}
    data["clarification"] = report(other).clarification_id
    assert invigilator.post(url("invigilator:report", exam), data).status_code == 404


def test_failed_report_keeps_input_and_client_key(invigilator: Client, exam: Exam) -> None:
    key = str(uuid.uuid4())
    data = {"client_key": key, "venue": "HALL1", "seat": "14", "label": "Q3b", "text": " "}
    response = invigilator.post(url("invigilator:report", exam), data)
    html = response.content.decode()
    assert response.status_code == 200
    assert "Describe the clarification with text or a photo." in html
    assert f'value="{key}"' in html
    assert '<details class="group new-question" id="new-question" open>' in html
    assert not Report.objects.exists()


def test_report_refuses_long_text_and_large_photos(
    invigilator: Client, exam: Exam, jpeg: bytes, media: Path, settings: Settings
) -> None:
    settings.EXAMDESK_PHOTO_UPLOAD_MAX_BYTES = len(jpeg) - 1
    seat = {"venue": "HALL1", "seat": "14", "label": "Q3b"}
    long_text = seat | {
        "client_key": str(uuid.uuid4()),
        "text": "x" * (settings.EXAMDESK_TEXT_MAX + 1),
    }
    photo = SimpleUploadedFile("q.jpg", jpeg, content_type="image/jpeg")
    large_photo = seat | {"client_key": str(uuid.uuid4()), "photo": photo}
    assert invigilator.post(url("invigilator:report", exam), long_text).status_code == 200
    assert (
        "That photo is too large."
        in invigilator.post(url("invigilator:report", exam), large_photo).content.decode()
    )
    assert not Report.objects.exists()


def test_deliver_list_shows_seats_with_counts(invigilator: Client, exam: Exam) -> None:
    q3 = report(exam)
    q5 = report(exam, label="Q5", text="Is y real?")
    other = report(exam, seat="21", clarification=q3.clarification)
    for r in (q3, q5):
        services.answer_clarification(
            r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
        )
    html = invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    assert '<span class="mt-0.5 block truncate">Yes</span>' in html
    assert "Is y real?" not in html
    assert 'aria-label="HALL1 14: 2 to deliver"' in html
    assert 'aria-label="HALL1 21: 1 to deliver"' in html
    assert html.index("HALL1 14:") < html.index("HALL1 21:")
    assert f'href="{seat_page(exam, "HALL1", "14").replace("&", "&amp;")}"' in html
    services.mark_delivered(
        other, answer=fresh(q3.clarification).current_answer, member=named(exam, "Ann")
    )
    html = invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    delivered = html[html.index('<details id="delivered"') :]
    assert 'Delivered recently<span class="count">1</span>' in delivered
    assert 'aria-label="HALL1 21: all delivered"' in delivered


def test_seat_page_marks_and_undoes(invigilator: Client, exam: Exam) -> None:
    q3 = report(exam)
    q5 = report(exam, label="Q5", text="Is y real?")
    answers = [
        services.answer_clarification(
            r.clarification, text=t, member=named(exam, "Prof"), expected_answer=None
        )
        for r, t in ((q3, "Yes"), (q5, "No"))
    ]
    page = invigilator.get(seat_page(exam, "HALL1", "14")).content.decode()
    assert page.count("Delivered to 14</button>") == 2
    assert '<span class="line-clamp-2">Is y real?</span>' in page
    assert "Delivered</h2>" not in page
    assert f'<a href="{url("invigilator:to_deliver", exam)}" class="mb-3' in page
    response = invigilator.post(
        url("invigilator:mark_delivered", exam, q3.pk), {"answer": answers[0].pk}, follow=True
    )
    assert response.redirect_chain[-1][0] == seat_page(exam, "HALL1", "14")  # stays
    html = response.content.decode()
    assert "Delivered Q3b to HALL1 14." in html
    assert html.count("Delivered to 14</button>") == 1
    delivered = html[html.index("Delivered</h2>") :]
    assert "<strong>Q3b</strong> Is x an integer?" in delivered
    assert '<span class="meta">Ann · ' in delivered
    assert "Undo</button>" in delivered
    response = invigilator.post(
        url("invigilator:mark_delivered", exam, q5.pk), {"answer": answers[1].pk}, follow=True
    )
    assert response.redirect_chain[-1][0] == seat_page(exam, "HALL1", "14")
    assert "All delivered to 14" in response.content.decode()
    delivery = q5.deliveries.get()
    response = invigilator.post(url("invigilator:undo_delivery", exam, delivery.pk), follow=True)
    assert response.redirect_chain[-1][0] == seat_page(exam, "HALL1", "14")
    html = response.content.decode()
    assert "HALL1 14 back to not delivered." in html
    assert not q5.deliveries.exists()
    invigilator.post(url("invigilator:mark_delivered", exam, q5.pk), {"answer": answers[1].pk})
    assert q5.deliveries.get().member.name == "Ann"


def test_a_delivered_seat_offers_the_next_seat(invigilator: Client, exam: Exam) -> None:
    with_venue(invigilator, exam, "HALL1")
    first = report(exam, seat="14")
    report(exam, seat="2", clarification=first.clarification)
    report(exam, seat="21", clarification=first.clarification)
    answer = services.answer_clarification(
        first.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    html = invigilator.post(
        url("invigilator:mark_delivered", exam, first.pk), {"answer": answer.pk}, follow=True
    ).content.decode()
    next_seat = seat_page(exam, "HALL1", "21").replace("&", "&amp;")
    assert f'href="{next_seat}">Next seat: 21' in html
    for r in Report.objects.exclude(seat="14"):
        services.mark_delivered(r, answer=answer, member=named(exam, "Ann"))
    html = invigilator.get(seat_page(exam, "HALL1", "14")).content.decode()
    assert "Next seat" not in html
    assert "Back to Deliver</a>" in html


def test_report_page_starts_blank_in_my_venue(invigilator: Client, exam: Exam) -> None:
    data = {"client_key": uuid.uuid4(), "venue": "HALL1", "seat": "14", "label": "Q3b", "text": "?"}
    response = invigilator.post(url("invigilator:report", exam), data)
    assert response["Location"] == url("invigilator:report_saved", exam, Report.objects.get().pk)
    page = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert 'class="match"' not in page
    assert not re.search(r'<input[^>]*name="seat"[^>]*value=', page)
    assert re.search(r'<input[^>]*name="seat"[^>]*autofocus', page)
    invigilator.post(url("invigilator:choose_venue", exam), {"venue": "HALL2"})
    page = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert '<input type="hidden" name="venue" value="HALL2">' in page


def test_locked_question_numbers_are_picked_from_a_list(invigilator: Client, exam: Exam) -> None:
    set_question_numbers(exam, "1-2", locked=True)
    html = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert re.search(r'<select name="label"[^>]*placeholder="e.g. 3b"', html)
    assert '<option value="General">General</option>' in html
    assert '<option value="Q2">Q2</option>' in html
    data = {"client_key": uuid.uuid4(), "venue": "HALL1", "seat": "14", "label": "3b", "text": "?"}
    html = invigilator.post(url("invigilator:report", exam), data).content.decode()
    assert "Pick a question from the list." in html
    assert not Report.objects.exists()
    response = invigilator.post(url("invigilator:report", exam), data | {"label": "2"})
    assert response.status_code == 302
    assert Report.objects.get().clarification.label == "Q2"


def test_a_page_from_before_the_lock_reloads(invigilator: Client, exam: Exam) -> None:
    set_question_numbers(exam, "1-2", locked=True)
    query = "?venue=HALL1&seat=14&label=13"
    response = invigilator.get(url("invigilator:report", exam) + query, headers=HTMX)
    assert response["HX-Redirect"] == url("invigilator:report", exam) + "?venue=HALL1&seat=14"
    page = invigilator.get(response["HX-Redirect"]).content.decode()
    assert "Pick the question from the examiners&#x27; list." in page
    assert re.search(r'<input[^>]*name="seat"[^>]*value="14"', page)


def test_unlocked_question_numbers_are_suggestions(invigilator: Client, exam: Exam) -> None:
    report(exam, label="Q7")
    set_question_numbers(exam, "1-2", locked=False)
    html = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert '<datalist id="labels"><option value="Q1"><option value="Q2"><option value="Q7">' in html
    assert re.search(r'<input[^>]*name="label"[^>]*list="labels"', html)
    data = {"client_key": uuid.uuid4(), "venue": "HALL1", "seat": "21", "label": "8", "text": "?"}
    assert invigilator.post(url("invigilator:report", exam), data).status_code == 302


def test_match_list_marks_what_the_seat_already_asked(invigilator: Client, exam: Exam) -> None:
    asked = report(exam)
    services.answer_clarification(
        asked.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    report(exam, seat="21", label="Q3a", text="Other seat")
    html = invigilator.get(
        url("invigilator:report", exam) + "?venue=HALL1&seat=14&label=Q3"
    ).content.decode()
    assert html.count("Asked from this seat</span>") == 1
    assert "{#" not in html  # a multi-line {# #} comment would show on the page
    seat_link = seat_page(exam, "HALL1", "14") + "&back=report"
    assert f'href="{seat_link.replace("&", "&amp;")}"><svg' in html
    assert html.count('<span class="match-action">Same question') == 1
    assert "Yes" not in html
    assert html.count("</svg>Open</span>") == 1
    assert '<span class="label-chip">Q3a</span>' in html
    fresh_seat = invigilator.get(
        url("invigilator:report", exam) + "?venue=HALL1&seat=34&label=Q3b"
    ).content.decode()
    assert "Asked from this seat</span>" not in fresh_seat
    assert fresh_seat.count('<span class="match-action">Same question') == 2
    assert '<span class="label-chip">Q3a</span>' in fresh_seat


def test_joining_an_answered_match_offers_its_answer(invigilator: Client, exam: Exam) -> None:
    answered = report(exam)
    answer = services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    waiting = report(exam, seat="21", label="Q3a")
    data = {"client_key": uuid.uuid4(), "venue": "HALL1", "seat": "34", "label": "Q3b"}
    response = invigilator.post(
        url("invigilator:report", exam), data | {"clarification": answered.clarification_id}
    )
    joined = Report.objects.get(seat="34")
    assert response["Location"] == url("invigilator:report_saved", exam, joined.pk)
    assert not joined.deliveries.exists()
    show = seat_page(exam, "HALL1", "34") + "&back=report"
    saved = invigilator.get(response["Location"]).content.decode()
    assert f'href="{show.replace("&", "&amp;")}"><svg' in saved
    assert "Report another</a>" in saved
    assert "Delete report</button>" in saved
    html = invigilator.get(show).content.decode()
    assert '<p class="answer-to-show">Yes</p>' in html
    assert "Delivered to 34</button>" in html
    assert '<input type="hidden" name="back" value="report">' in html
    assert f'<a href="{url("invigilator:report", exam)}" class="mb-3' in html
    marked = {"answer": answer.pk, "back": "report"}
    response = invigilator.post(url("invigilator:mark_delivered", exam, joined.pk), marked)
    assert response["Location"] == show
    data = {"client_key": uuid.uuid4(), "venue": "HALL1", "seat": "45", "label": "Q3a"}
    response = invigilator.post(
        url("invigilator:report", exam),
        data | {"clarification": waiting.clarification_id},
        follow=True,
    )
    html = response.content.decode()
    assert "HALL1 45 · Q3a</p>" in html
    assert "Show the answer" not in html


def test_tab_bar_badges_count_my_venue(invigilator: Client, exam: Exam) -> None:
    with_venue(invigilator, exam, "HALL1")
    answered = report(exam)
    services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    report(exam, seat="21", label="Q5")  # open, in my venue
    report(exam, venue="HALL2", seat="1", label="Q6")  # open, other venue
    services.announce(text="Typo", member=named(exam, "Prof"))
    html = invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    assert '<span class="count" title="Waiting for an answer">1</span>' in html
    assert '<span class="count action" title="Seats to deliver">1</span>' in html
    assert '<span class="count urgent" title="To read out">1</span>' in html
    assert re.search(r'href="[^"]+/open/" aria-current="page"', html)
    refresh = invigilator.get(url("invigilator:report", exam), headers=REFRESH)
    assert '<div id="tab-bar" data-live>' in refresh.content.decode()


def test_tab_bar_alerts_newest_unread_announcement(invigilator: Client, exam: Exam) -> None:
    services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    newest = services.announce(text="30 minutes left", member=named(exam, "Prof"))
    page = url("invigilator:to_deliver", exam)
    html = invigilator.get(page).content.decode()
    assert f'data-alert="{newest.pk}.0"' in html
    assert "1 more" in html
    assert "Mark as read out</a>" in html
    with_venue(invigilator, exam, "HALL1")
    html = invigilator.get(page, headers=REFRESH).content.decode()
    assert "Read out in HALL1" in html
    assert f'<input type="hidden" name="next" value="{page}">' in html
    here = url("invigilator:to_deliver", exam)
    response = invigilator.post(url("invigilator:mark_read_out", exam, newest.pk), {"next": here})
    assert response["Location"] == here
    on_tab = invigilator.get(url("invigilator:announcements", exam)).content.decode()
    assert "data-alert" not in on_tab
    services.mark_read_out(
        exam.announcements.get(text="Typo in Q2"), venue="HALL1", member=named(exam, "Ann")
    )
    assert "data-alert" not in invigilator.get(page).content.decode()


def test_open_screen_lists_open_seats_in_my_venue(invigilator: Client, exam: Exam) -> None:
    with_venue(invigilator, exam, "HALL1")
    report(exam)
    report(exam, venue="HALL2", seat="1", label="Q6")
    html = invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    assert '<span class="place-chip">14</span>' in html  # my venue only: no venue name
    assert '<span class="place-chip">1</span>' not in html
    with_venue(invigilator, exam, "HALL3")
    assert (
        "Nothing waiting in HALL3"
        in invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    )


def test_open_groups_clarifications_by_question(invigilator: Client, exam: Exam) -> None:
    report(exam, label="Q10", text="Ten?")
    report(exam, seat="21", label="Q2", text="Two?")
    report(exam, seat="34", label="Q2", text="Also two?")
    html = invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    headings = re.findall(
        r'<h2 class="section-title">(Q\w+)<span class="count">(\d+)</span></h2>', html
    )
    assert headings == [("Q2", "2"), ("Q10", "1")]
    q2 = html[html.index(">Q2<span") : html.index(">Q10<span")]
    assert q2.index("Two?") < q2.index("Also two?")


def test_open_groups_seats_by_venue_and_deliver_names_them_in_full(
    invigilator: Client, exam: Exam
) -> None:
    first = report(exam)
    report(exam, seat="21", clarification=first.clarification)
    report(exam, venue="HALL2", seat="1", clarification=first.clarification)
    html = invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    chip = '<span class="place-chip"><span class="font-normal text-subtle">{}</span>{}</span>'
    assert html.index(chip.format("HALL1", "14")) < html.index(chip.format("HALL1", "21"))
    assert chip.format("HALL2", "1") in html
    services.answer_clarification(
        first.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    html = invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    assert 'aria-label="HALL2 1: 1 to deliver"' in html
    heading = '<h2 class="section-title">' + re.escape(icon("map-pin")) + r"(\w+)</h2>"
    assert re.findall(heading, html) == ["HALL1", "HALL2"]
    with_venue(invigilator, exam, "HALL1")
    assert not re.findall(
        heading, invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    )


def test_announcements_paginate_newest_first(
    invigilator: Client, exam: Exam, settings: Settings
) -> None:
    settings.EXAMDESK_PAGE_SIZE = 1
    services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    services.announce(text="Ten minutes left", member=named(exam, "Prof"))
    first = invigilator.get(url("invigilator:announcements", exam)).content.decode()
    assert "Ten minutes left" in first
    assert "Typo in Q2" not in first
    assert 'href="?page=2" rel="next"' in first
    second = invigilator.get(url("invigilator:announcements", exam) + "?page=2").content.decode()
    assert "Typo in Q2" in second
    assert "Ten minutes left" not in second


def test_announcements_unread_then_marked_as_announced(invigilator: Client, exam: Exam) -> None:
    announcement = services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    unset = invigilator.get(url("invigilator:announcements", exam)).content.decode()
    assert f'commandfor="read-out-{announcement.pk}" command="show-modal"' in unset
    html = invigilator.post(
        url("invigilator:mark_read_out", exam, announcement.pk), {"venue": ""}, follow=True
    ).content.decode()
    assert "Pick the venue where it was read out." in html
    assert not announcement.readouts.exists()
    invigilator.post(url("invigilator:mark_read_out", exam, announcement.pk), {"venue": "hall 1"})
    assert announcement.readouts.get().venue == "HALL1"
    assert member(invigilator, exam).venue == "HALL1"
    html = invigilator.post(
        url("invigilator:undo_read_out", exam, announcement.pk), follow=True
    ).content.decode()
    assert "HALL1 back to not read out." in html
    assert not announcement.readouts.exists()
    unread = invigilator.get(url("invigilator:announcements", exam)).content.decode()
    assert "</svg>Read out in HALL1</button>" in unread
    response = invigilator.post(url("invigilator:mark_read_out", exam, announcement.pk))
    assert response["Location"] == url("invigilator:announcements", exam)
    read = invigilator.get(url("invigilator:announcements", exam)).content.decode()
    assert 'aria-label="Undo read out in HALL1"' in read
    assert re.search(r'</svg>HALL1 · Ann · <span class="num">\d\d:\d\d</span>', read)


def test_invigilator_and_examiner_remove_a_seat(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    first = report(exam)
    joined = report(exam, seat="15", clarification=first.clarification)
    matches = f"{url('invigilator:report', exam)}?venue=HALL1&seat=15&label=Q3b"
    html = invigilator.get(matches).content.decode()
    assert f'commandfor="delete-report-{joined.pk}" command="show-modal">Delete report<' in html
    assert ">Delete the report from HALL1 15?</h2>" in html
    response = invigilator.post(
        url("delete_report", exam, joined.pk), {"next": matches}, follow=True
    )
    assert response.redirect_chain[-1][0] == matches
    assert "Deleted the report from HALL1 15." in response.content.decode()
    assert not Report.objects.filter(pk=joined.pk).exists()
    page = url("examiner:clarification", exam, first.clarification_id)
    assert (
        f'<dialog id="delete-report-{first.pk}" class="modal" aria-labelledby'
        in examiner.get(page).content.decode()
    )
    response = examiner.post(url("delete_report", exam, first.pk), {"next": page})
    assert response["Location"] == url("examiner:clarifications", exam)
    assert not Clarification.objects.exists()


def test_first_invigilator_screen_asks_for_venue_once_with_quick_picks(
    invigilator: Client, exam: Exam
) -> None:
    report(exam, venue="HALL2", seat="1")
    first = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert '<dialog id="venue-dialog" class="modal" data-autoopen aria-labelledby' in first
    assert 'form="venue-pick" name="pick" value="HALL2"' in first
    assert (
        'modal" data-autoopen'
        not in invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    )
    invigilator.post(url("invigilator:choose_venue", exam), {"pick": "HALL2", "venue": ""})
    assert member(invigilator, exam).venue == "HALL2"
    invigilator.post(url("invigilator:choose_venue", exam), {"all": "1"})
    assert member(invigilator, exam).venue == ""


def test_unlocked_venues_are_suggestions(invigilator: Client, exam: Exam) -> None:
    report(exam, venue="GYM")
    set_venues(exam, ["HALL2", "HALL10"], locked=False)
    html = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert re.findall(r'name="pick" value="(\w+)"', html) == ["GYM", "HALL2", "HALL10"]
    assert '<datalist id="venues"><option value="GYM"><option value="HALL2">' in html
    assert 'name="venue" value="" list="venues"' in html
    invigilator.post(url("invigilator:choose_venue", exam), {"venue": "hall 3"})
    assert member(invigilator, exam).venue == "HALL3"


def test_locked_venues_are_picked_from_the_list(invigilator: Client, exam: Exam) -> None:
    report(exam, venue="GYM")
    set_venues(exam, ["HALL1", "HALL2"], locked=True)
    page = url("invigilator:report", exam)
    html = invigilator.get(page).content.decode()
    assert re.findall(r'name="pick" value="(\w+)"', html) == ["HALL1", "HALL2"]
    assert 'list="venues"' not in html
    assert 'name="all" value="1">I move between venues</button>' in html
    assert ">Save</button>" not in html
    assert re.search(r'<select name="venue"[^>]*placeholder="e.g. HALL1"', html)
    data = {"client_key": uuid.uuid4(), "venue": "GYM", "seat": "14", "label": "2", "text": "?"}
    assert "Pick a venue from the list." in invigilator.post(page, data).content.decode()
    assert invigilator.post(page, data | {"venue": "HALL2"}).status_code == 302
    invigilator.post(url("invigilator:choose_venue", exam), {"pick": "HALL2"})
    assert member(invigilator, exam).venue == "HALL2"
    invigilator.post(url("invigilator:choose_venue", exam), {"all": "1"})
    assert member(invigilator, exam).venue == ""


def test_a_venue_typed_before_the_lock_is_refused_and_asked_again(
    invigilator: Client, exam: Exam
) -> None:
    with_venue(invigilator, exam, "HALL1")
    set_venues(exam, ["HALL1", "HALL2"], locked=True)
    next_page = url("invigilator:to_deliver", exam)
    response = invigilator.post(
        url("invigilator:choose_venue", exam), {"venue": "gym", "next": next_page}, follow=True
    )
    assert response.redirect_chain[-1][0] == next_page
    html = response.content.decode()
    assert "Pick your venue from the examiners&#x27; list." in html
    assert '<dialog id="venue-dialog" class="modal" data-autoopen' in html


def test_locking_asks_invigilators_in_other_venues_again(exam: Exam) -> None:
    member_venues = {"Ann": "HALL1", "Ben": "GYM", "Cy": "", "Dee": None}
    for name, venue in member_venues.items():
        services.set_venue(named(exam, name), venue)
    set_venues(exam, ["HALL1", "HALL2"], locked=True)
    assert {m.name: m.venue for m in exam.members.all()} == member_venues | {"Ben": None}
    set_venues(exam, ["HALL2"], locked=True)
    assert {m.name: m.venue for m in exam.members.all()} == {
        "Ann": "HALL2",
        "Ben": None,
        "Cy": "HALL2",
        "Dee": None,
    }


def test_one_locked_venue_is_set_with_no_picker(invigilator: Client, exam: Exam) -> None:
    set_venues(exam, ["HALL1"], locked=True)
    html = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert member(invigilator, exam).venue == "HALL1"
    assert 'id="venue-dialog"' not in html
    assert "venue-picker-button" not in html
    assert '<span class="sr-only">Venue: </span><span class="truncate">HALL1</span>' in html
    assert '<input type="hidden" name="venue" value="HALL1">' in html
    invigilator.post(url("invigilator:choose_venue", exam), {"all": "1"})
    assert member(invigilator, exam).venue == "HALL1"


def test_one_unlocked_venue_is_preset(invigilator: Client, exam: Exam) -> None:
    set_venues(exam, ["HALL1"], locked=False)
    html = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert member(invigilator, exam).venue == "HALL1"
    assert '<dialog id="venue-dialog" class="modal" aria-labelledby' in html  # not opened
    assert 'aria-label="Venue: HALL1"' in html
    invigilator.post(url("invigilator:choose_venue", exam), {"venue": "gym"})
    assert member(invigilator, exam).venue == "GYM"


def test_locked_venues_mark_read_out_from_the_list(invigilator: Client, exam: Exam) -> None:
    announcement = services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    set_venues(exam, ["HALL1", "HALL2"], locked=True)
    html = invigilator.get(url("invigilator:announcements", exam)).content.decode()
    assert '<option value="HALL2">HALL2</option>' in html
    read_out_url = url("invigilator:mark_read_out", exam, announcement.pk)
    invigilator.post(read_out_url, {"venue": "GYM"})
    assert not announcement.readouts.exists()
    invigilator.post(read_out_url, {"venue": "HALL2"})
    assert announcement.readouts.get().venue == "HALL2"


def test_match_step_groups_answered_and_open(invigilator: Client, exam: Exam) -> None:
    answered = report(exam, text="Answered one?")
    services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    report(exam, seat="21", label="Q3a", text="Open one?")
    html = invigilator.get(
        url("invigilator:report", exam) + "?venue=HALL1&seat=34&label=3"
    ).content.decode()
    assert html.index(">Already asked about Q3</h2>") < html.index("Answered one?")
    assert html.index("Answered one?") < html.index("Open one?")
    assert "New clarification</summary>" in html
    assert '<details class="group new-question" id="new-question">' in html


def test_typed_question_puts_the_most_alike_match_first(invigilator: Client, exam: Exam) -> None:
    answered = report(exam, seat="21", text="Can we use a calculator?")
    services.answer_clarification(
        answered.clarification, text="No", member=named(exam, "Prof"), expected_answer=None
    )
    report(exam, seat="22", label="Q3a", text="Is x an integer?")
    step_two = url("invigilator:report", exam) + "?venue=HALL1&seat=34&label=Q3"
    untyped = invigilator.get(step_two).content.decode()
    assert untyped.index("calculator") < untyped.index("integer")
    typed = invigilator.get(step_two + "&text=Must+x+be+a+whole+number%3F").content.decode()
    assert typed.index(">Already asked about Q3</h2>") < typed.index("integer")
    assert typed.index("integer") < typed.index("calculator")
    alike = round(similarity("Must x be a whole number?", "Is x an integer?") * 100)
    assert f'<span class="meta ml-auto">{alike}% alike</span>' in typed
    assert "% alike" not in untyped
    assert ">Must x be a whole number?</textarea>" in typed


def test_typed_question_offers_alike_clarifications_on_other_questions(
    invigilator: Client, exam: Exam
) -> None:
    report(exam, seat="21", label="Q5", text="Is x an integer?")
    report(exam, seat="22", label="Q6", text="Can we use a calculator?")
    step_two = url("invigilator:report", exam) + "?venue=HALL1&seat=34&label=Q3"
    assert '<span class="label-chip">Q5</span>' not in invigilator.get(step_two).content.decode()
    html = invigilator.get(step_two + "&text=Is+x+an+integer+or+a+real%3F").content.decode()
    assert html.index(">1 under other questions<svg") < html.index("Is x an integer?")
    assert '<span class="label-chip">Q5</span>' in html
    assert "calculator" not in html


def test_matches_after_the_first_three_are_collapsed_under_show_more(
    invigilator: Client, exam: Exam
) -> None:
    questions = ["Is x real?", "Is x an integer?", "Is x positive?", "Is x odd?"]
    for seat, text in enumerate(questions, start=21):
        report(exam, seat=str(seat), text=text)
    query = "?venue=HALL1&seat=34&label=Q3"
    html = invigilator.get(url("invigilator:report", exam) + query).content.decode()
    show_more = html.index(">Show 1 more<svg")
    assert html.index("Is x positive?") < show_more < html.index("Is x odd?")
    assert html.count('<span class="match-action">Same question') == 4


def test_typed_question_collapses_matches_that_are_not_similar(
    invigilator: Client, exam: Exam
) -> None:
    report(exam, seat="21", text="Is x an integer?")
    report(exam, seat="22", text="Can we use a calculator?")
    step_two = url("invigilator:report", exam) + "?venue=HALL1&seat=34&label=Q3&text={}"
    html = invigilator.get(step_two.format("Is+x+an+integer+or+a+real%3F")).content.decode()
    show_more = html.index(">Show 1 more<svg")
    assert html.index("Is x an integer?") < show_more < html.index("calculator")
    unrelated = invigilator.get(step_two.format("Is+the+room+cold%3F")).content.decode()
    assert unrelated.index(">Already asked about Q3</h2>") < unrelated.index(">Show all 2<svg")


def test_typing_refreshes_only_the_matches(invigilator: Client, exam: Exam) -> None:
    report(exam, seat="21", text="Is x an integer?")
    query = "?venue=HALL1&seat=34&label=Q3&text=integer"
    html = invigilator.get(url("invigilator:report", exam) + query, headers=HTMX).content.decode()
    assert '<span class="match-action">Same question' in html
    assert "<h1" not in html
    assert "Save clarification" not in html


def test_text_naming_another_question_warns(invigilator: Client, exam: Exam) -> None:
    report_url = url("invigilator:report", exam) + "?venue=HALL1&seat=34&label={}&text={}"
    html = invigilator.get(
        report_url.format("", "5(b) mentions Figure 2"), headers=HTMX
    ).content.decode()
    assert "The question mentions Q5b." in html
    html = invigilator.get(report_url.format("5", "5(b) mentions Figure 2")).content.decode()
    assert "The question mentions" not in html


def test_deliver_badge_and_home_count_seats_not_questions(
    client: Client, invigilator: Client, exam: Exam, staff: User
) -> None:
    for label in ("Q1", "Q2"):
        r = report(exam, label=label)
        services.answer_clarification(
            r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
        )
    html = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert '<span class="count action" title="Seats to deliver">1</span>' in html
    client.force_login(staff)
    home = client.get(reverse("home")).content.decode()
    assert 'tabular-nums max-sm:hidden text-blue-700">1</td>' in home


def test_opening_a_seat_to_deliver_shows_other_tas_it_is_taken(
    invigilator: Client, exam: Exam
) -> None:
    clarification = report(exam).clarification
    services.answer_clarification(
        clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    seat = url("invigilator:deliver_seat", exam) + "?venue=HALL1&seat=14"
    taken = 'aria-label="HALL1 14: 1 to deliver, with {}"'
    ben = Client()
    open_link(ben, exam.invigilator_token, "Ben")
    invigilator.get(seat, headers=REFRESH)  # a refresh takes nothing
    assert ", with " not in ben.get(url("invigilator:to_deliver", exam)).content.decode()
    invigilator.get(seat)
    html = ben.get(url("invigilator:to_deliver", exam)).content.decode()
    assert taken.format("Ann") in html
    assert 'class="deliver-row claimed"' in html
    assert re.search(
        r'<div id="live" data-live data-refresh-in="([1-9]\d*)"', html
    )  # when the claim expires
    # Ben opening it anyway takes it over, and is told.
    assert "<strong>Ann</strong> was delivering to this seat" in ben.get(seat).content.decode()
    assert "was delivering" not in ben.get(seat).content.decode()  # now Ben's
    # Ann's next screen leaves her seat, which was Ben's by then; Ben's next screen leaves it.
    invigilator.get(url("invigilator:open_clarifications", exam))
    assert (
        taken.format("Ben") in invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    )
    ben.get(url("invigilator:report", exam))
    assert ", with " not in invigilator.get(url("invigilator:to_deliver", exam)).content.decode()


def test_a_seat_claim_lapses(invigilator: Client, exam: Exam) -> None:
    clarification = report(exam).clarification
    services.answer_clarification(
        clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    ben = services.add_member(exam, name="Ben", role=Role.INVIGILATOR)
    services.claim_seat(ben, venue="HALL1", seat="14")
    assert ", with Ben" in invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    # A newer answer: nobody has seen it yet.
    services.answer_clarification(
        clarification,
        text="No",
        member=named(exam, "Prof"),
        expected_answer=fresh(clarification).current_answer_id,
    )
    assert ", with " not in invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    # A phone put away on the seat.
    services.claim_seat(ben, venue="HALL1", seat="14")
    exam.claims.update(claimed_at=timezone.now() - settings.EXAMDESK_SEAT_CLAIM_FOR)
    assert ", with " not in invigilator.get(url("invigilator:to_deliver", exam)).content.decode()


def test_a_seat_claim_follows_a_renamed_member(invigilator: Client, exam: Exam) -> None:
    clarification = report(exam).clarification
    services.answer_clarification(
        clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    invigilator.get(url("invigilator:deliver_seat", exam) + "?venue=HALL1&seat=14")
    invigilator.post(url("rename_member", exam), {"name": "Ann Tan"})
    ben = Client()
    open_link(ben, exam.invigilator_token, "Ben")
    assert ", with Ann Tan" in ben.get(url("invigilator:to_deliver", exam)).content.decode()
    invigilator.get(url("invigilator:report", exam))
    assert ", with " not in ben.get(url("invigilator:to_deliver", exam)).content.decode()


def test_snooze_puts_off_the_alert_on_this_device(
    invigilator: Client, exam: Exam, settings: Settings
) -> None:
    with_venue(invigilator, exam, "HALL1")
    announcement = services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    page = url("invigilator:report", exam)
    assert f'data-alert="{announcement.pk}.0"' in invigilator.get(page).content.decode()
    later = url("invigilator:snooze_announcement", exam, announcement.pk)
    assert invigilator.post(later, {"next": url("invigilator:report", exam)})["Location"] == url(
        "invigilator:report", exam
    )
    html = invigilator.get(page).content.decode()
    assert "data-alert" not in html
    assert '<span class="count urgent" title="To read out">1</span>' in html
    assert re.search(
        r'<div id="announcement-bar" data-live data-refresh-in="[1-9]\d*"', html
    )  # when it returns
    # Once the delay is over it returns, under a new key so that it vibrates again.
    settings.EXAMDESK_SNOOZE_FOR = timedelta(0)
    invigilator.post(later)
    html = invigilator.get(page).content.decode()
    assert re.search(rf'data-alert="{announcement.pk}\.[1-9]\d*"', html)


def test_account_menu_offers_examiners_the_examiner_view(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    examiner_view = url("examiner:clarifications", exam)
    item = f'<li class="sm:hidden"><a href="{examiner_view}">Examiner view</a></li>'
    assert item in examiner.get(url("invigilator:report", exam)).content.decode()
    assert item not in examiner.get(examiner_view).content.decode()
    assert "Examiner view" not in invigilator.get(url("invigilator:report", exam)).content.decode()
