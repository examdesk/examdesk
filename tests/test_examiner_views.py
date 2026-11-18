"""The examiner screens."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pytest_django.fixtures import Settings

from examdesk import services
from examdesk.models import Exam, Readout, Report
from examdesk.similarity import similarity
from tests.helpers import (
    REFRESH,
    fresh,
    icon_then,
    member,
    named,
    open_link,
    report,
    url,
)

pytestmark = pytest.mark.django_db


def test_delete_requires_typed_name(examiner: Client, exam: Exam) -> None:
    services.close_exam(exam)
    examiner.post(url("examiner:delete_exam", exam), {"name": "wrong"})
    assert Exam.objects.exists()
    response = examiner.post(url("examiner:delete_exam", exam), {"name": exam.name})
    assert response["Location"] == reverse("home")
    assert not Exam.objects.exists()


def test_send_stays_on_the_clarification(examiner: Client, exam: Exam) -> None:
    first = report(exam).clarification
    report(exam, seat="C1")
    response = examiner.post(
        url("examiner:answer_clarification", exam, first.pk), {"text": "Yes"}, follow=True
    )
    assert response.redirect_chain[-1][0] == url("examiner:clarification", exam, first.pk)
    assert "Sent." in response.content.decode()


def test_clarification_filters_come_back_on_the_plain_url(examiner: Client, exam: Exam) -> None:
    report(exam, label="Q3")
    page = url("examiner:clarifications", exam)
    assert examiner.get(page).status_code == 200
    examiner.get(page + "?state=all&label=Q3&sort=seats")
    examiner.get(page + "?state=open", headers=REFRESH)  # a refresh does not save
    response = examiner.get(page)
    assert response.status_code == 302
    assert response["Location"] == page + "?state=all&label=Q3&sort=seats"
    # Going back to the plain URL shows it as it was.
    back = examiner.get(page, headers={"HX-Request": "true", "HX-History-Restore-Request": "true"})
    assert back.status_code == 200


def test_a_saved_filter_forgets_a_label_that_is_gone(examiner: Client, exam: Exam) -> None:
    r = report(exam, label="Q3b")
    page = url("examiner:clarifications", exam)
    examiner.get(page + "?state=all&label=Q3b&sort=seats")
    services.relabel_clarification(r.clarification, "Q4")
    html = examiner.get(page, follow=True).content.decode()
    assert "not one of the available choices" not in html
    assert '<input type="hidden" name="state" value="all"' in html  # the other filters still apply
    assert '<option value="seats" selected>' in html
    assert examiner.get(page)["Location"] == page + "?state=all&sort=seats"


def test_question_filter_picks_several_questions(examiner: Client, exam: Exam) -> None:
    report(exam, label="Q3b", text="Three?")
    report(exam, seat="C1", label="Q5", text="Five?")
    report(exam, seat="D4", label="Q31", text="Thirty-one?")
    html = examiner.get(
        url("examiner:clarifications", exam) + "?label=Q3&label=Q5&state=all"
    ).content.decode()
    assert "Three?" in html  # Q3 covers Q3b
    assert "Five?" in html
    assert "Thirty-one?" not in html
    select = re.search(r'<select name="label".*?</select>', html, re.DOTALL)
    assert select
    options = re.findall(r'<option value="(\w+)"( selected)?>', select[0])
    assert options == [("Q3", " selected"), ("Q3b", ""), ("Q5", " selected"), ("Q31", "")]


def test_clarification_list_shows_state_chips(examiner: Client, exam: Exam) -> None:
    answered = report(exam).clarification
    services.answer_clarification(
        answered, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    announced = report(exam, seat="C1").clarification
    services.announce(text="!", member=named(exam, "Prof"), clarification=announced)
    html = examiner.get(url("examiner:clarifications", exam) + "?state=all").content.decode()
    assert icon_then("check", "Answered</span>") in html
    assert icon_then("megaphone", "Announced</span>") in html


def test_invalid_relabel_reopens_its_dialog_with_the_error(examiner: Client, exam: Exam) -> None:
    clarification = report(exam).clarification
    response = examiner.post(
        url("examiner:relabel_clarification", exam, clarification.pk), {"label": "x!?"}
    )
    html = response.content.decode()
    assert response.status_code == 400
    assert '<dialog id="relabel-dialog" class="modal" data-autoopen aria-labelledby' in html
    assert "Not a question" in html
    assert 'value="x!?"' in html


def test_wrong_delete_name_reopens_its_dialog(examiner: Client, exam: Exam) -> None:
    services.close_exam(exam)
    response = examiner.post(url("examiner:delete_exam", exam), {"name": "CS2109S"})
    html = response.content.decode()
    assert response.status_code == 400
    assert '<dialog id="delete-dialog" class="modal" data-autoopen aria-labelledby' in html
    assert 'value="CS2109S"' in html
    assert Exam.objects.exists()


def test_wording_may_be_cleared(examiner: Client, exam: Exam) -> None:
    clarification = report(exam).clarification
    examiner.post(url("examiner:reword_clarification", exam, clarification.pk), {"text": " "})
    clarification.refresh_from_db()
    assert clarification.wording == ""


def test_members_page_shows_the_invigilator_link_then_the_examiner_link_with_a_warning(
    examiner: Client, exam: Exam
) -> None:
    html = examiner.get(url("examiner:members", exam)).content.decode()
    invigilator_link = reverse("join", args=[exam.invigilator_token])
    examiner_link = reverse("join", args=[exam.examiner_token])
    warning = "Full control of the exam."
    assert html.index(invigilator_link) < html.index(warning) < html.index(examiner_link)
    assert html.count(warning) == 1
    assert '<dialog id="invite-dialog" class="modal" aria-labelledby' in html
    invite = examiner.get(url("examiner:members", exam) + "?invite=examiner").content.decode()
    assert '<dialog id="invite-dialog" class="modal" data-autoopen aria-labelledby' in invite
    assert 'value="examiner" class="sr-only" checked>Examiners</label>' in invite
    assert 'value="invigilator" class="sr-only">Invigilators</label>' in invite
    assert 'commandfor="invigilator-qr" command="show-modal"' in html
    assert 'commandfor="examiner-qr" command="show-modal"' in html


def test_examiner_announcements_page(invigilator: Client, examiner: Client, exam: Exam) -> None:
    report(exam, venue="LT20", seat="A1")
    assert invigilator.get(url("examiner:announcements", exam)).status_code == 403
    response = examiner.post(url("examiner:announce", exam), {"text": "Typo in Q2"})
    assert response["Location"] == url("examiner:announcements", exam)
    html = examiner.get(url("examiner:announcements", exam)).content.decode()
    assert "Typo in Q2" in html
    assert '<span class="badge state-to-read-out" title="Not read out yet">' in html
    assert icon_then("clock", "LT20</span>") in html
    assert '<span class="count" title="Not read out in every venue yet">1</span>' in html
    queue = examiner.get(url("examiner:clarifications", exam)).content.decode()
    assert "Typo in Q2" not in queue
    assert "Announce to all venues" not in queue


def test_seats_list_every_venue_and_find_a_seat(
    examiner: Client, exam: Exam, settings: Settings
) -> None:
    settings.EXAMDESK_PAGE_SIZE = 2
    report(exam, venue="LT20", seat="A1")
    report(exam, seat="B14")
    report(exam, seat="B2")
    seats = url("examiner:seats", exam)
    every = examiner.get(seats).content.decode()
    assert every.index('aria-label="LT19 B2:') < every.index('aria-label="LT19 B14:')
    assert 'aria-label="LT20 A1:' in every
    assert 'href="#venue-lt20">LT20</a>' in every
    found = examiner.get(seats + "?q=b+1").content.decode()
    assert 'value="B1"' in found
    assert 'aria-label="LT19 B14:' in found
    assert 'aria-label="LT19 B2:' not in found
    assert 'aria-label="LT20 A1:' not in found
    assert "No seats match" in examiner.get(seats + "?q=Z9").content.decode()


def test_seats_show_each_seat_by_its_least_advanced_question(examiner: Client, exam: Exam) -> None:
    done = report(exam)
    answer = services.answer_clarification(
        done.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    services.mark_delivered(done, answer=answer, member=named(exam, "Ann"))
    report(exam, label="Q5", text="Five?")  # B14 asks again: open
    report(exam, venue="LT20", seat="A1", clarification=done.clarification)  # answered, to deliver
    html = examiner.get(url("examiner:seats", exam)).content.decode()
    assert re.findall(r'<section class="card-box scroll-mt-4" id="venue-(\w+)"', html) == [
        "lt19",
        "lt20",
    ]
    assert (
        'aria-label="LT19 B14: waiting, 2 questions">B14<span class="seat-pill-count">2</span>'
        in html
    )
    assert 'class="seat-pill state-to-deliver"' in html
    assert 'aria-label="LT20 A1: to deliver">A1</a>' in html
    assert 'aria-current="true">All<span class="num">2</span>' in html
    to_deliver = examiner.get(url("examiner:seats", exam) + "?state=to-deliver").content.decode()
    assert 'aria-label="LT20 A1:' in to_deliver
    assert 'aria-label="LT19 B14:' not in to_deliver
    assert "No seats to deliver" in to_deliver
    page = examiner.get(url("examiner:seat", exam) + "?venue=LT20&seat=A1").content.decode()
    assert ">Yes</p>" in page
    corrected = fresh(done.clarification).current_answer
    services.answer_clarification(
        done.clarification,
        text="No",
        member=named(exam, "Prof"),
        expected_answer=corrected.pk if corrected else None,
    )
    page = examiner.get(url("examiner:seat", exam) + "?venue=LT19&seat=B14").content.decode()
    assert "<s>Yes</s></p>" in page
    assert "Delivered by Ann at" in page
    assert "since corrected" in page


def test_deleting_an_announcement_asks_first(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    posted = services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    services.mark_read_out(
        posted, venue="LT19", member=named(exam, "Ann")
    )  # read out: still deletable
    page = examiner.get(url("examiner:announcements", exam)).content.decode()
    assert f'commandfor="delete-{posted.pk}" command="show-modal"' in page
    assert f'<dialog id="delete-{posted.pk}" class="modal" aria-labelledby' in page
    live = examiner.get(url("examiner:announcements", exam), headers=REFRESH)
    assert f'commandfor="delete-{posted.pk}"' in live.content.decode()  # the live part alone
    assert invigilator.post(url("examiner:delete_announcement", exam, posted.pk)).status_code == 403
    response = examiner.post(url("examiner:delete_announcement", exam, posted.pk), follow=True)
    html = response.content.decode()
    assert "Deleted the announcement." in html
    assert not exam.announcements.exists()
    assert not Readout.objects.exists()


def test_announcements_paginate_newest_first(
    examiner: Client, exam: Exam, settings: Settings
) -> None:
    settings.EXAMDESK_PAGE_SIZE = 1
    services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    services.announce(text="Ten minutes left", member=named(exam, "Prof"))
    first = examiner.get(url("examiner:announcements", exam)).content.decode()
    assert "Ten minutes left" in first
    assert "Typo in Q2" not in first
    second = examiner.get(url("examiner:announcements", exam) + "?page=2").content.decode()
    assert "Typo in Q2" in second
    assert "Ten minutes left" not in second


def test_clarifications_sort_by_question_and_tabs_count_each_state(
    examiner: Client, exam: Exam
) -> None:
    report(exam, label="Q10", text="Ten?")
    report(exam, seat="C1", label="Q2", text="Two?")
    answered = report(exam, seat="D4", label="Q3", text="Three?")
    services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    page = url("examiner:clarifications", exam)
    html = examiner.get(page + "?sort=question").content.decode()
    assert html.index("Two?") < html.index("Ten?")
    assert "Three?" not in html
    assert '<option value="question" selected>Question</option>' in html
    tabs = re.findall(r'href="\?([^"]+)"[^>]*>(\w+)<span class="num">(\d+)</span>', html)
    assert tabs == [
        ("sort=question&amp;state=open", "Open", "2"),
        ("sort=question&amp;state=answered", "Answered", "1"),
        ("sort=question&amp;state=closed", "Closed", "0"),
    ]
    assert 'aria-current="true">Open<span class="num">2</span>' in html
    html = examiner.get(page + "?state=all&sort=question").content.decode()
    assert html.index("Two?") < html.index("Three?") < html.index("Ten?")
    html = examiner.get(page + "?state=answered").content.decode()
    assert "Three?" in html
    assert "Two?" not in html
    assert 'title="Open clarifications" data-open-count="2">2</span>' in html


def test_clarification_page_links_keep_the_sort(
    examiner: Client, exam: Exam, settings: Settings
) -> None:
    settings.EXAMDESK_PAGE_SIZE = 2
    for label in ("Q1", "Q2", "Q3"):
        report(exam, label=label, text=f"{label}?")
    clarifications = url("examiner:clarifications", exam)
    first = examiner.get(clarifications + "?sort=question").content.decode()
    assert "Q2?" in first
    assert "Q3?" not in first
    assert 'href="?sort=question&amp;page=2" rel="next"' in first
    second = examiner.get(clarifications + "?sort=question&page=2").content.decode()
    assert "Q3?" in second
    assert "Q2?" not in second
    assert 'href="?sort=question&amp;page=1" rel="prev"' in second
    assert "<span>3\N{EN DASH}3 of 3</span>" in second
    refresh = examiner.get(clarifications + "?sort=question&page=2", headers=REFRESH)
    assert "Q3?" in refresh.content.decode()


def test_relabel_changes_the_clarifications_label(examiner: Client, exam: Exam) -> None:
    clarification = report(exam).clarification
    response = examiner.post(
        url("examiner:relabel_clarification", exam, clarification.pk), {"label": "5"}, follow=True
    )
    assert response.redirect_chain[-1][0] == url("examiner:clarification", exam, clarification.pk)
    assert "Moved from Q3b to Q5." in response.content.decode()
    assert fresh(clarification).label == "Q5"
    html = examiner.get(url("examiner:clarification", exam, clarification.pk)).content.decode()
    assert 'name="label" value="Q5"' in html


def test_withdraw_answer_and_reopen(examiner: Client, exam: Exam) -> None:
    r = report(exam)
    page = url("examiner:clarification", exam, r.clarification_id)
    answer = services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    assert "Withdraw</button>" in examiner.get(page).content.decode()
    withdraw = url("examiner:withdraw_answer", exam, r.clarification_id)
    html = examiner.post(withdraw, {"expected_answer": answer.pk}, follow=True).content.decode()
    assert "Answer withdrawn." in html
    assert fresh(r.clarification).current_answer is None
    services.announce(text="!", member=named(exam, "Prof"), clarification=r.clarification)
    reopen = url("examiner:reopen_clarification", exam, r.clarification_id)
    assert "Reopen</button>" in examiner.get(page).content.decode()
    html = examiner.post(reopen, follow=True).content.decode()
    assert "Reopened Q3b." in html
    assert fresh(r.clarification).closed_reason == ""
    delivered = services.answer_clarification(
        r.clarification, text="No", member=named(exam, "Prof"), expected_answer=None
    )
    services.mark_delivered(r, answer=delivered, member=named(exam, "Ann"))
    assert "Withdraw</button>" not in examiner.get(page).content.decode()


def test_move_stays_on_the_split_clarification_while_it_has_seats(
    examiner: Client, exam: Exam
) -> None:
    first = report(exam)
    second = report(exam, seat="C1", clarification=first.clarification)
    response = examiner.post(url("examiner:move_report", exam, second.pk), {"target": "new"})
    assert response["Location"] == url("examiner:clarification", exam, first.clarification_id)
    moved = Report.objects.get(pk=second.pk).clarification
    assert moved != first.clarification
    response = examiner.post(url("examiner:move_report", exam, first.pk), {"target": moved.pk})
    # Moving the last seat closes the source, so follow the seat.
    assert response["Location"] == url("examiner:clarification", exam, moved.pk)


def test_move_to_an_unknown_clarification_is_refused(examiner: Client, exam: Exam) -> None:
    first = report(exam)
    report(exam, seat="C1", clarification=first.clarification)
    other_exam = services.create_exam(name="Other", owner=exam.owner)
    for target in ("", "999", str(report(other_exam).clarification_id)):
        html = examiner.post(
            url("examiner:move_report", exam, first.pk), {"target": target}, follow=True
        )
        assert "Pick a clarification to move the seat to." in html.content.decode()
    assert Report.objects.get(pk=first.pk).clarification_id == first.clarification_id
    assert exam.clarifications.count() == 1


def test_merge_asks_for_a_target_first(examiner: Client, exam: Exam) -> None:
    a = report(exam)
    report(exam, seat="C1")
    assert (
        '<option value="">Pick a clarification'
        in examiner.get(url("examiner:clarification", exam, a.clarification_id)).content.decode()
    )
    back = url("examiner:clarification", exam, a.clarification_id)
    response = examiner.post(
        url("examiner:merge_clarifications", exam), {"source": a.clarification_id, "next": back}
    )
    assert response["Location"] == back
    assert fresh(a.clarification).closed_reason == ""


def test_move_offers_new_clarification_only_with_several_seats(
    examiner: Client, exam: Exam
) -> None:
    single = report(exam).clarification
    option = '<option value="new">New clarification</option>'
    assert (
        option not in examiner.get(url("examiner:clarification", exam, single.pk)).content.decode()
    )
    report(exam, seat="C1", clarification=single)
    assert option in examiner.get(url("examiner:clarification", exam, single.pk)).content.decode()


def test_announce_from_a_clarification_uses_the_answer_box(examiner: Client, exam: Exam) -> None:
    clarification = report(exam).clarification
    html = examiner.get(url("examiner:clarification", exam, clarification.pk)).content.decode()
    assert 'commandfor="announce-dialog" command="show-modal"><svg' in html
    assert "Announce to all venues…</button>" in html
    assert '<blockquote data-quote="answer-text"></blockquote>' in html
    assert '<input type="hidden" name="text" data-quote="answer-text">' in html
    response = examiner.post(
        url("examiner:announce_clarification", exam, clarification.pk),
        {"text": "Typo in Q3", "expected_answer": ""},
    )
    assert response["Location"] == url("examiner:clarification", exam, clarification.pk)
    assert exam.announcements.get().text == "Typo in Q3"


def test_queue_shows_seats_to_deliver_and_wait(examiner: Client, exam: Exam) -> None:
    r = report(exam)
    report(exam, seat="C1", clarification=r.clarification)
    services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    html = examiner.get(url("examiner:clarifications", exam) + "?state=all").content.decode()
    assert '<span class="ml-auto font-medium text-blue-700">2 to deliver</span>' in html
    assert 'title="Seats"' in html
    assert "data-wait-since" not in html  # answered: no wait
    open_one = report(exam, seat="D4", label="Q5").clarification
    html = examiner.get(url("examiner:clarifications", exam) + "?state=open").content.decode()
    assert re.search(r'<span class="wait ml-auto " data-wait-since="\d+" data-wait-level', html)
    assert f'href="{url("examiner:clarification", exam, open_one.pk)}"' in html


def test_clarification_page_lists_what_seats_asked_and_duplicates(
    examiner: Client, exam: Exam
) -> None:
    first = report(exam, text="Is x an integer?")
    report(exam, seat="C1", clarification=first.clarification, text="Can x be negative?")
    other = report(exam, seat="D4", label="Q3a", text="Is y real?")
    report(exam, seat="E5", label="Q4", text="Elsewhere?")
    mislabeled = report(exam, seat="E6", label="Q4", text="Is x an integer or a real?")
    announced = report(exam, seat="F6", label="Q3c", text="Already announced?").clarification
    services.announce(text="!", member=named(exam, "Prof"), clarification=announced)
    merged = report(exam, seat="G7", label="Q3c", text="Merged away?").clarification
    services.merge_clarifications(sources=[merged], target=other.clarification)
    html = examiner.get(
        url("examiner:clarification", exam, first.clarification_id)
    ).content.decode()
    seats = html[html.index('id="seats-title"') : html.index("Possible duplicates")]
    assert seats.index("LT19 B14</a>") < seats.index("LT19 C1</a>")
    assert "Can x be negative?" in seats
    assert "Is x an integer?" not in seats  # the wording, shown above
    duplicates = html[html.index("Possible duplicates") : html.index('id="correction"')]
    assert "Is y real?" in duplicates
    assert "Elsewhere?" not in duplicates
    assert duplicates.index("Is x an integer or a real?") < duplicates.index("Is y real?")
    assert f'name="target" value="{mislabeled.clarification_id}"' in duplicates
    alike = round(similarity("Is x an integer?", "Is x an integer or a real?") * 100)
    assert f">{alike}% alike<" in duplicates
    assert f'<input type="hidden" name="target" value="{other.clarification_id}">' in duplicates
    assert f'name="target" value="{announced.pk}"' in duplicates
    assert f'name="source" value="{other.clarification_id}"' in duplicates
    assert (
        f'name="source" value="{announced.pk}" aria-labelledby="duplicate-{announced.pk}" disabled>'
        in duplicates
    )
    assert "Merged away?" not in duplicates


def test_merge_several_and_show_the_target(examiner: Client, exam: Exam) -> None:
    here, a, b = report(exam), report(exam, seat="C1"), report(exam, seat="D4")
    response = examiner.post(
        url("examiner:merge_clarifications", exam),
        {"source": [a.clarification_id, b.clarification_id], "target": here.clarification_id},
    )
    assert response["Location"] == url("examiner:clarification", exam, here.clarification_id)
    assert set(here.clarification.reports.all()) == {here, a, b}


def test_failed_merge_stays_on_the_page(examiner: Client, exam: Exam) -> None:
    here = report(exam).clarification
    back = url("examiner:clarification", exam, here.pk)
    response = examiner.post(
        url("examiner:merge_clarifications", exam),
        {"source": here.pk, "target": here.pk, "next": back},
    )
    assert response["Location"] == back
    assert fresh(here).closed_reason == ""


def test_announced_seats_wait_on_the_seats_pages_until_read_out(
    examiner: Client, exam: Exam
) -> None:
    r = report(exam)
    announcement = services.announce(
        text="Typo", member=named(exam, "Prof"), clarification=r.clarification
    )
    seats = examiner.get(url("examiner:seats", exam)).content.decode()
    assert 'aria-label="LT19 B14: waiting"' in seats
    seat = examiner.get(url("examiner:seat", exam) + "?venue=LT19&seat=B14").content.decode()
    assert icon_then("megaphone", "To read out</span>") in seat
    services.mark_read_out(announcement, venue="LT19", member=named(exam, "Ann"))
    seats = examiner.get(url("examiner:seats", exam)).content.decode()
    assert 'aria-label="LT19 B14: delivered"' in seats
    page = examiner.get(url("examiner:clarification", exam, r.clarification_id)).content.decode()
    assert 'class="badge state-read-out mt-0.5"' in page


def test_members_page_lists_everyone_online_first(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    assert invigilator.get(url("examiner:members", exam)).status_code == 403
    invigilator.post(url("invigilator:choose_venue", exam), {"venue": "lt 19"}, follow=True)
    ben = Client()
    open_link(ben, exam.invigilator_token, "Ben")
    ben.get(url("invigilator:report", exam))
    html = examiner.get(url("examiner:members", exam)).content.decode()
    html = html[html.index('<h1 class="truncate">People</h1>') :]
    assert html.count(">Online<") == 3  # Ann, Ben and Prof
    assert html.index("Ann") < html.index('<span class="badge">LT19</span>') < html.index("Ben")
    assert 'Invigilators<span class="num">2</span>' in html
    invigilators = examiner.get(url("examiner:members", exam) + "?role=invigilator")
    assert "Prof" not in invigilators.content.decode().split("<tbody>")[1]
    # Leaving, or a phone put away, shows when they were last seen.
    ben.post(url("leave_exam", exam))
    exam.members.exclude(name="Prof").update(
        last_seen=timezone.now() - settings.EXAMDESK_ONLINE_FOR
    )
    html = examiner.get(url("examiner:members", exam)).content.decode()
    assert html.count(">Online<") == 1
    assert html.count(">Last seen ") == 2


def test_removing_a_member_signs_out_their_devices(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    ann = member(invigilator, exam)
    html = examiner.get(url("examiner:members", exam)).content.decode()
    assert f'commandfor="remove-member-{ann.pk}"' in html
    assert f'commandfor="remove-member-{member(examiner, exam).pk}"' not in html
    assert invigilator.post(url("examiner:remove_member", exam, ann.pk)).status_code == 403
    response = examiner.post(url("examiner:remove_member", exam, ann.pk), follow=True)
    assert "Removed Ann." in response.content.decode()
    assert invigilator.get(url("invigilator:report", exam)).status_code == 404
    own = member(examiner, exam).pk
    assert examiner.post(url("examiner:remove_member", exam, own)).status_code == 404


def test_a_removed_member_keeps_their_history_and_can_rejoin(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    ann = member(invigilator, exam)
    r = report(exam)
    assert r.member == ann
    members = url("examiner:members", exam)
    examiner.post(url("examiner:remove_member", exam, ann.pk))
    assert f'commandfor="remove-member-{ann.pk}"' not in examiner.get(members).content.decode()
    page = url("examiner:clarification", exam, r.clarification_id)
    assert '<span class="meta">Ann · ' in examiner.get(page).content.decode()
    join = reverse("join", args=[exam.invigilator_token])
    assert "Ann is already on this exam" in invigilator.post(join, {"name": "Ann"}).content.decode()
    assert invigilator.post(join, {"name": "Ann", "same": "1"}).status_code == 302
    assert member(invigilator, exam).pk == ann.pk
    assert invigilator.get(url("invigilator:report", exam)).status_code == 200
    assert f'commandfor="remove-member-{ann.pk}"' in examiner.get(members).content.decode()


def test_clarification_shows_five_seats_and_the_rest_under_show_more(
    examiner: Client, exam: Exam
) -> None:
    first = report(exam, seat="A1")
    for n in range(2, 8):
        report(exam, seat=f"A{n}", clarification=first.clarification)
    html = examiner.get(
        url("examiner:clarification", exam, first.clarification_id)
    ).content.decode()
    more = html[html.index('<details class="group/more">') :]
    assert "Show 2 more</summary>" in more
    assert "LT19 A5</a>" not in more
    assert "LT19 A6</a>" in more


def test_photo_shows_as_the_question_without_wording(
    examiner: Client, exam: Exam, media: Path, jpeg: bytes
) -> None:
    asked = report(exam, text="", photo=SimpleUploadedFile("p.jpg", jpeg, "image/jpeg"))
    html = examiner.get(
        url("examiner:clarification", exam, asked.clarification_id)
    ).content.decode()
    question = html[html.index('aria-label="Question"') : html.index('id="seats-title"')]
    assert f'src="{url("photo", exam, asked.pk)}"' in question
    assert html.count(f'src="{url("photo", exam, asked.pk)}"') == 1
