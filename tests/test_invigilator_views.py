"""The invigilator screens."""

import re
import uuid
from datetime import timedelta
from pathlib import Path

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
    fresh,
    icon_then,
    member,
    named,
    open_link,
    report,
    seat_page,
    url,
    with_venue,
)

pytestmark = pytest.mark.django_db


def test_report_retry_does_not_duplicate(invigilator: Client, exam: Exam) -> None:
    data = {"client_key": uuid.uuid4(), "venue": "lt 19", "seat": "b 14", "label": "3(b)"}
    data["text"] = "Is x an integer?"
    locations = {
        invigilator.post(url("invigilator:report", exam), data)["Location"] for _ in range(2)
    }
    r = Report.objects.get()
    assert (r.venue, r.seat, r.clarification.label, r.member.name) == ("LT19", "B14", "Q3b", "Ann")
    assert locations == {url("invigilator:report_saved", exam, r.pk)}


def test_cannot_join_clarification_of_other_exam(invigilator: Client, exam: Exam) -> None:
    other = services.create_exam(name="Other", owner=exam.owner)
    data = {"client_key": uuid.uuid4(), "venue": "LT19", "seat": "B14", "label": "Q3b"}
    data["clarification"] = report(other).clarification_id
    assert invigilator.post(url("invigilator:report", exam), data).status_code == 404


def test_failed_report_keeps_input_and_client_key(invigilator: Client, exam: Exam) -> None:
    key = str(uuid.uuid4())
    data = {"client_key": key, "venue": "LT19", "seat": "B14", "label": "Q3b", "text": " "}
    response = invigilator.post(url("invigilator:report", exam), data)
    html = response.content.decode()
    assert response.status_code == 200
    assert "Describe the clarification with text or a photo." in html
    assert f'value="{key}"' in html
    assert "Save clarification" in html
    assert not Report.objects.exists()


def test_report_refuses_long_text_and_large_photos(
    invigilator: Client, exam: Exam, jpeg: bytes, media: Path, settings: Settings
) -> None:
    settings.EXAMDESK_PHOTO_UPLOAD_MAX_BYTES = len(jpeg) - 1
    seat = {"venue": "LT19", "seat": "B14", "label": "Q3b"}
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
    other = report(exam, seat="C1", clarification=q3.clarification)
    for r in (q3, q5):
        services.answer_clarification(
            r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
        )
    html = invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    assert "Yes" not in html
    assert "Is y real?" not in html
    assert 'aria-label="LT19 B14: 2 to deliver"' in html
    assert 'aria-label="LT19 C1: 1 to deliver"' in html
    assert html.index("LT19 B14:") < html.index("LT19 C1:")
    assert f'href="{seat_page(exam, "LT19", "B14").replace("&", "&amp;")}"' in html
    services.mark_delivered(
        other, answer=fresh(q3.clarification).current_answer, member=named(exam, "Ann")
    )
    html = invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    delivered = html[html.index('<details id="delivered"') :]
    assert "Recently delivered (1)" in delivered
    assert 'aria-label="LT19 C1: all delivered"' in delivered


def test_seat_page_accordion_marks_and_unmarks(invigilator: Client, exam: Exam) -> None:
    q3 = report(exam)
    q5 = report(exam, label="Q5", text="Is y real?")
    answers = [
        services.answer_clarification(
            r.clarification, text=t, member=named(exam, "Prof"), expected_answer=None
        )
        for r, t in ((q3, "Yes"), (q5, "No"))
    ]

    def row(r: Report, state: str = "") -> str:
        return f'<details id="q-{r.pk}" name="clarifications" class="clarification{state}"'

    page = invigilator.get(seat_page(exam, "LT19", "B14")).content.decode()
    assert page.count('class="clarification"') == 2
    assert "<strong>Q5</strong> Is y real?" in page
    assert row(q3) + " open>" in page
    assert row(q5) + ">" in page
    assert "Delivered to B14</button>" in page
    assert f'href="{url("invigilator:to_deliver", exam)}" aria-label="Back"' in page
    response = invigilator.post(
        url("invigilator:mark_delivered", exam, q3.pk), {"answer": answers[0].pk}, follow=True
    )
    assert response.redirect_chain[-1][0] == seat_page(exam, "LT19", "B14", q3)  # stays
    html = response.content.decode()
    assert "Delivered Q3b to LT19 B14." in html
    assert row(q3, " done") + " open>" in html
    assert row(q5) + ">" in html
    done = invigilator.get(seat_page(exam, "LT19", "B14", q3)).content.decode()
    assert 'Delivered by <strong class="font-medium text-base-content">Ann</strong>' in done
    assert "Unmark</button>" in done
    response = invigilator.post(
        url("invigilator:mark_delivered", exam, q5.pk), {"answer": answers[1].pk}
    )
    assert response["Location"] == seat_page(exam, "LT19", "B14", q5)
    delivery = q5.deliveries.get()
    response = invigilator.post(url("invigilator:unmark_delivered", exam, delivery.pk), follow=True)
    assert response.redirect_chain[-1][0] == seat_page(exam, "LT19", "B14", q5)
    html = response.content.decode()
    assert "Unmarked LT19 B14." in html
    assert not q5.deliveries.exists()
    invigilator.post(url("invigilator:mark_delivered", exam, q5.pk), {"answer": answers[1].pk})
    assert q5.deliveries.get().member.name == "Ann"


def test_report_edit_shows_step_one_with_values(invigilator: Client, exam: Exam) -> None:
    report(exam)
    query = "?venue=LT19&seat=B20&label=Q3b"
    assert (
        'class="match"' in invigilator.get(url("invigilator:report", exam) + query).content.decode()
    )
    step_two = invigilator.get(url("invigilator:report", exam) + query).content.decode()
    back = re.search(
        r'<a class="btn btn-outline btn-square" href="([^"]+)" aria-label="Back to step 1"',
        step_two,
    )
    assert back
    html = invigilator.get(
        url("invigilator:report", exam) + back[1].replace("&amp;", "&")
    ).content.decode()
    assert 'class="match"' not in html
    assert re.search(r'<input[^>]*name="seat"[^>]*value="B20"', html)
    assert re.search(r'<input[^>]*name="label"[^>]*value="Q3b"', html)


def test_report_page_keeps_the_last_seat_and_question(invigilator: Client, exam: Exam) -> None:
    data = {"client_key": uuid.uuid4(), "venue": "LT19", "seat": "B14", "label": "Q3b", "text": "?"}
    response = invigilator.post(url("invigilator:report", exam), data)
    assert response["Location"] == url("invigilator:report_saved", exam, Report.objects.get().pk)
    step_one = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert 'class="match"' not in step_one
    assert re.search(r'<input[^>]*name="venue"[^>]*value="LT19"', step_one)
    assert re.search(r'<input[^>]*name="seat"[^>]*value="B14"', step_one)
    assert re.search(r'<input[^>]*name="label"[^>]*value="Q3b"', step_one)
    assert re.search(r'<input[^>]*name="label"[^>]*autofocus', step_one)
    assert not re.search(r'<input[^>]*name="seat"[^>]*autofocus', step_one)
    invigilator.post(url("rename_member", exam), {"name": "Ann Tan"})
    renamed = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert re.search(r'<input[^>]*name="seat"[^>]*value="B14"', renamed)
    invigilator.post(url("invigilator:choose_venue", exam), {"venue": "LT20"})
    elsewhere = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert not re.search(r'<input[^>]*name="seat"[^>]*value="B14"', elsewhere)


def test_match_list_marks_what_the_seat_already_asked(invigilator: Client, exam: Exam) -> None:
    asked = report(exam)
    services.answer_clarification(
        asked.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    report(exam, seat="C1", label="Q3a", text="Other seat")
    html = invigilator.get(
        url("invigilator:report", exam) + "?venue=LT19&seat=B14&label=Q3"
    ).content.decode()
    assert html.count("Asked from this seat</span>") == 1
    assert "{#" not in html  # a multi-line {# #} comment would show on the page
    seat_link = seat_page(exam, "LT19", "B14", asked) + "&back=report"
    assert f'<a class="match" href="{seat_link.replace("&", "&amp;")}">' in html
    assert html.count('<button class="match">') == 1
    assert "Yes" not in html
    assert html.count("</svg>Open</span>") == 1
    assert '<span class="badge">Q3a</span>' in html
    fresh_seat = invigilator.get(
        url("invigilator:report", exam) + "?venue=LT19&seat=D4&label=Q3b"
    ).content.decode()
    assert "Asked from this seat" not in fresh_seat
    assert fresh_seat.count('<button class="match">') == 2
    assert '<span class="badge">Q3a</span>' in fresh_seat


def test_joining_an_answered_match_offers_its_answer(invigilator: Client, exam: Exam) -> None:
    answered = report(exam)
    answer = services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    waiting = report(exam, seat="C1", label="Q3a")
    data = {"client_key": uuid.uuid4(), "venue": "LT19", "seat": "D4", "label": "Q3b"}
    response = invigilator.post(
        url("invigilator:report", exam), data | {"clarification": answered.clarification_id}
    )
    joined = Report.objects.get(seat="D4")
    assert response["Location"] == url("invigilator:report_saved", exam, joined.pk)
    assert not joined.deliveries.exists()
    show = seat_page(exam, "LT19", "D4", joined) + "&back=report"
    saved = invigilator.get(response["Location"]).content.decode()
    assert f'href="{show.replace("&", "&amp;")}"><svg' in saved
    assert "Report another question" in saved
    assert "Delete this report</button>" in saved
    html = invigilator.get(show).content.decode()
    assert '<p class="show-panel">Yes</p>' in html
    assert "Delivered to D4</button>" in html
    assert '<input type="hidden" name="back" value="report">' in html
    assert f'href="{url("invigilator:report", exam)}" aria-label="Back"' in html
    marked = {"answer": answer.pk, "back": "report"}
    response = invigilator.post(url("invigilator:mark_delivered", exam, joined.pk), marked)
    assert response["Location"] == show
    data = {"client_key": uuid.uuid4(), "venue": "LT19", "seat": "E5", "label": "Q3a"}
    response = invigilator.post(
        url("invigilator:report", exam),
        data | {"clarification": waiting.clarification_id},
        follow=True,
    )
    html = response.content.decode()
    assert "LT19 E5 · Q3a</h1>" in html
    assert "Show the answer" not in html


def test_tab_bar_badges_count_my_venue(invigilator: Client, exam: Exam) -> None:
    with_venue(invigilator, exam, "LT19")
    answered = report(exam)
    services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    report(exam, seat="C1", label="Q5")  # open, in my venue
    report(exam, venue="LT20", seat="A1", label="Q6")  # open, other venue
    services.announce(text="Typo", member=named(exam, "Prof"))
    bar = invigilator.get(
        url("invigilator:tab_bar", exam) + "?tab=open_clarifications&v=", headers=HTMX
    ).content.decode()
    assert '<span class="count" title="Open clarifications">1</span>' in bar
    assert '<span class="count action" title="Seats to deliver">1</span>' in bar
    assert '<span class="count urgent" title="To read out">1</span>' in bar
    assert re.search(r'href="[^"]+/open/" aria-current="page"', bar)
    page = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert 'hx-trigger="load"' in page
    assert 'class="count' not in page.split('class="dock"')[1].split("</nav>")[0]


def test_tab_bar_alerts_newest_unread_announcement(invigilator: Client, exam: Exam) -> None:
    services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    newest = services.announce(text="30 minutes left", member=named(exam, "Prof"))
    tabs = url("invigilator:tab_bar", exam) + "?tab=to_deliver&here=/somewhere/&v="
    bar = invigilator.get(tabs, headers=HTMX).content.decode()
    assert f'data-alert="{newest.pk}.0"' in bar
    assert "1 more" in bar
    assert "Open Announcements" in bar
    with_venue(invigilator, exam, "LT19")
    bar = invigilator.get(tabs, headers=HTMX).content.decode()
    assert "Read out in LT19" in bar
    assert '<input type="hidden" name="next" value="/somewhere/">' in bar
    here = url("invigilator:to_deliver", exam)
    response = invigilator.post(url("invigilator:mark_read_out", exam, newest.pk), {"next": here})
    assert response["Location"] == here
    on_tab = invigilator.get(
        url("invigilator:tab_bar", exam) + "?tab=announcements&v=", headers=HTMX
    ).content.decode()
    assert "data-alert" not in on_tab
    services.mark_read_out(
        exam.announcements.get(text="Typo in Q2"), venue="LT19", member=named(exam, "Ann")
    )
    assert "data-alert" not in invigilator.get(tabs, headers=HTMX).content.decode()


def test_open_screen_lists_open_seats_in_my_venue(invigilator: Client, exam: Exam) -> None:
    with_venue(invigilator, exam, "LT19")
    report(exam)
    report(exam, venue="LT20", seat="A1", label="Q6")
    html = invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    assert '<span class="badge state-open">B14</span>' in html
    assert not re.search(
        re.escape(icon("map-pin")) + r"(\w+)</span>", html
    )  # my venue only: no venue name
    assert '<span class="badge state-open">A1</span>' not in html
    with_venue(invigilator, exam, "LT21")
    assert (
        "No open clarifications in LT21<"
        in invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    )


def test_open_groups_clarifications_by_question(invigilator: Client, exam: Exam) -> None:
    report(exam, label="Q10", text="Ten?")
    report(exam, seat="C1", label="Q2", text="Two?")
    report(exam, seat="D4", label="Q2", text="Also two?")
    html = invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    assert re.findall(r'<h2 class="group-heading">(Q\w+)</h2>', html) == ["Q2", "Q10"]
    q2 = html[html.index(">Q2</h2>") : html.index(">Q10</h2>")]
    assert q2.index("Two?") < q2.index("Also two?")


def test_open_groups_seats_by_venue_and_deliver_names_them_in_full(
    invigilator: Client, exam: Exam
) -> None:
    first = report(exam)
    report(exam, seat="C1", clarification=first.clarification)
    report(exam, venue="LT20", seat="A1", clarification=first.clarification)
    html = invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    venues = re.findall(re.escape(icon("map-pin")) + r"(\w+)</span>", html)
    assert venues == ["LT19", "LT20"]
    lt19 = html[html.index("LT19</span>") : html.index("LT20</span>")]
    assert lt19.count('class="badge state-open"') == 2
    services.answer_clarification(
        first.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    html = invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    assert 'aria-label="LT20 A1: 1 to deliver"' in html
    venues = re.findall(
        '<h2 class="venue-heading">' + re.escape(icon("map-pin")) + r"(\w+)</h2>", html
    )
    assert venues == ["LT19", "LT20"]
    with_venue(invigilator, exam, "LT19")
    assert (
        'class="venue-heading"'
        not in invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    )


def test_announcements_unread_then_marked_as_announced(invigilator: Client, exam: Exam) -> None:
    announcement = services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    unset = invigilator.get(url("invigilator:announcements", exam)).content.decode()
    assert f'commandfor="read-out-{announcement.pk}" command="show-modal"' in unset
    html = invigilator.post(
        url("invigilator:mark_read_out", exam, announcement.pk), {"venue": ""}, follow=True
    ).content.decode()
    assert "Pick the venue where it was read out." in html
    assert not announcement.readouts.exists()
    invigilator.post(url("invigilator:mark_read_out", exam, announcement.pk), {"venue": "lt 19"})
    assert announcement.readouts.get().venue == "LT19"
    assert member(invigilator, exam).venue == "LT19"
    html = invigilator.post(
        url("invigilator:unmark_read_out", exam, announcement.pk), follow=True
    ).content.decode()
    assert "Unmarked in LT19." in html
    assert not announcement.readouts.exists()
    unread = invigilator.get(url("invigilator:announcements", exam)).content.decode()
    assert "</svg>Read out in LT19</button>" in unread
    response = invigilator.post(url("invigilator:mark_read_out", exam, announcement.pk))
    assert response["Location"] == url("invigilator:announcements", exam)
    read = invigilator.get(url("invigilator:announcements", exam)).content.decode()
    assert 'aria-label="Read out in LT19: unmark"' in read
    assert icon_then("map-pin", "LT19</span>", "Venue") in read
    assert icon_then("user", "Ann</span>", "Read out by") in read
    assert "<title>Read out at</title>" in read


def test_invigilator_and_examiner_remove_a_seat(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    first = report(exam)
    joined = report(exam, seat="B15", clarification=first.clarification)
    matches = f"{url('invigilator:report', exam)}?venue=LT19&seat=B15&label=Q3b"
    html = invigilator.get(matches).content.decode()
    assert (
        f'commandfor="delete-report-{joined.pk}" command="show-modal">Delete this seat\'s report<'
        in html
    )
    assert ">Delete the report from LT19 B15?</h2>" in html
    response = invigilator.post(
        url("delete_report", exam, joined.pk), {"next": matches}, follow=True
    )
    assert response.redirect_chain[-1][0] == matches
    assert "Deleted the report from LT19 B15." in response.content.decode()
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
    report(exam, venue="LT20", seat="A1")
    first = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert '<dialog id="venue-dialog" class="modal" data-autoopen aria-labelledby' in first
    assert 'form="venue-pick" name="pick" value="LT20"' in first
    assert (
        'modal" data-autoopen'
        not in invigilator.get(url("invigilator:open_clarifications", exam)).content.decode()
    )
    invigilator.post(url("invigilator:choose_venue", exam), {"pick": "LT20", "venue": ""})
    assert member(invigilator, exam).venue == "LT20"
    invigilator.post(url("invigilator:choose_venue", exam), {"all": "1"})
    assert member(invigilator, exam).venue == ""


def test_match_step_groups_answered_and_open(invigilator: Client, exam: Exam) -> None:
    answered = report(exam, text="Answered one?")
    services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    report(exam, seat="C1", label="Q3a", text="Open one?")
    html = invigilator.get(
        url("invigilator:report", exam) + "?venue=LT19&seat=D4&label=3"
    ).content.decode()
    assert html.index(">Answered</h2>") < html.index("Answered one?")
    assert html.index("Answered one?") < html.index(">Open</h2>")
    assert html.index(">Open</h2>") < html.index("Open one?")
    assert "New clarification</summary>" in html


def test_typed_question_puts_the_most_alike_match_first(invigilator: Client, exam: Exam) -> None:
    answered = report(exam, seat="C1", text="Can we use a calculator?")
    services.answer_clarification(
        answered.clarification, text="No", member=named(exam, "Prof"), expected_answer=None
    )
    report(exam, seat="C2", label="Q3a", text="Is x an integer?")
    step_two = url("invigilator:report", exam) + "?venue=LT19&seat=D4&label=Q3"
    untyped = invigilator.get(step_two).content.decode()
    assert untyped.index("calculator") < untyped.index("integer")
    typed = invigilator.get(step_two + "&text=Must+x+be+a+whole+number%3F").content.decode()
    assert typed.index(">Under Q3</h2>") < typed.index("integer")
    assert typed.index("integer") < typed.index("calculator")
    assert ">Answered</h2>" not in typed
    alike = round(similarity("Must x be a whole number?", "Is x an integer?") * 100)
    assert f'<span class="meta">{alike}% alike</span>' in typed
    assert "% alike" not in untyped
    assert ">Must x be a whole number?</textarea>" in typed


def test_typed_question_offers_alike_clarifications_on_other_questions(
    invigilator: Client, exam: Exam
) -> None:
    report(exam, seat="C1", label="Q5", text="Is x an integer?")
    report(exam, seat="C2", label="Q6", text="Can we use a calculator?")
    step_two = url("invigilator:report", exam) + "?venue=LT19&seat=D4&label=Q3"
    assert '<span class="badge">Q5</span>' not in invigilator.get(step_two).content.decode()
    html = invigilator.get(step_two + "&text=Is+x+an+integer+or+a+real%3F").content.decode()
    assert html.index(">Under another question</h2>") < html.index("Is x an integer?")
    assert '<span class="badge">Q5</span>' in html
    assert "calculator" not in html


def test_typing_refreshes_only_the_matches(invigilator: Client, exam: Exam) -> None:
    report(exam, seat="C1", text="Is x an integer?")
    query = "?venue=LT19&seat=D4&label=Q3&text=integer"
    html = invigilator.get(url("invigilator:report", exam) + query, headers=HTMX).content.decode()
    assert '<button class="match">' in html
    assert "<h1" not in html
    assert "Save clarification" not in html


def test_deliver_badge_and_home_count_seats_not_questions(
    client: Client, invigilator: Client, exam: Exam, staff: User
) -> None:
    for label in ("Q1", "Q2"):
        r = report(exam, label=label)
        services.answer_clarification(
            r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
        )
    bar = invigilator.get(
        url("invigilator:tab_bar", exam) + "?tab=report&v=", headers=HTMX
    ).content.decode()
    assert '<span class="count action" title="Seats to deliver">1</span>' in bar
    client.force_login(staff)
    home = client.get(reverse("home")).content.decode()
    assert "<strong>1</strong><span>seat to deliver</span>" in home


def test_opening_a_seat_to_deliver_shows_other_tas_it_is_taken(
    invigilator: Client, exam: Exam
) -> None:
    clarification = report(exam).clarification
    services.answer_clarification(
        clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    seat = url("invigilator:deliver_seat", exam) + "?venue=LT19&seat=B14"
    taken = 'aria-label="LT19 B14: 1 to deliver, with {}"'
    ben = Client()
    open_link(ben, exam.invigilator_token, "Ben")
    invigilator.get(seat + "&v=1", headers=HTMX)  # a poll takes nothing
    assert ", with " not in ben.get(url("invigilator:to_deliver", exam)).content.decode()
    invigilator.get(seat)
    html = ben.get(url("invigilator:to_deliver", exam)).content.decode()
    assert taken.format("Ann") in html
    assert 'class="seat-card claimed"' in html
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
    services.claim_seat(ben, venue="LT19", seat="B14")
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
    services.claim_seat(ben, venue="LT19", seat="B14")
    exam.claims.update(claimed_at=timezone.now() - settings.EXAMDESK_SEAT_CLAIM_FOR)
    assert ", with " not in invigilator.get(url("invigilator:to_deliver", exam)).content.decode()


def test_a_seat_claim_follows_a_renamed_member(invigilator: Client, exam: Exam) -> None:
    clarification = report(exam).clarification
    services.answer_clarification(
        clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    invigilator.get(url("invigilator:deliver_seat", exam) + "?venue=LT19&seat=B14")
    invigilator.post(url("rename_member", exam), {"name": "Ann Tan"})
    ben = Client()
    open_link(ben, exam.invigilator_token, "Ben")
    assert ", with Ann Tan" in ben.get(url("invigilator:to_deliver", exam)).content.decode()
    invigilator.get(url("invigilator:report", exam))
    assert ", with " not in ben.get(url("invigilator:to_deliver", exam)).content.decode()


def test_snooze_puts_off_the_alert_on_this_device(
    invigilator: Client, exam: Exam, settings: Settings
) -> None:
    with_venue(invigilator, exam, "LT19")
    announcement = services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    bar = url("invigilator:tab_bar", exam) + "?tab=report&v="
    assert (
        f'data-alert="{announcement.pk}.0"' in invigilator.get(bar, headers=HTMX).content.decode()
    )
    later = url("invigilator:snooze_announcement", exam, announcement.pk)
    assert invigilator.post(later, {"next": url("invigilator:report", exam)})["Location"] == url(
        "invigilator:report", exam
    )
    html = invigilator.get(bar, headers=HTMX).content.decode()
    assert "data-alert" not in html
    assert '<span class="count urgent" title="To read out">1</span>' in html
    # Once the delay is over it returns, under a new key so that it vibrates again.
    settings.EXAMDESK_SNOOZE_FOR = timedelta(0)
    invigilator.post(later)
    html = invigilator.get(bar, headers=HTMX).content.decode()
    assert re.search(rf'data-alert="{announcement.pk}\.[1-9]\d*"', html)
