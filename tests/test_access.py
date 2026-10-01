"""Joining, roles, names, polling, and pages across both roles."""

import importlib
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from pytest_django import DjangoAssertNumQueries
from pytest_django.fixtures import Settings

from examdesk import services, session
from examdesk.models import Clarification, Exam
from tests.helpers import HTMX, icon_then, named, open_link, poll_url, report, url, with_venue

pytestmark = pytest.mark.django_db


def test_link_asks_for_a_name_per_exam_then_opens_role_page(client: Client, exam: Exam) -> None:
    join = reverse("join", args=[exam.invigilator_token])
    assert 'name="name"' in client.get(join).content.decode()
    assert client.get(url("exam", exam)).status_code == 404
    assert client.post(join, {"name": " Ann  Lee "})["Location"] == url("exam", exam)
    assert exam.members.get().name == "Ann Lee"
    assert client.get(url("exam", exam))["Location"] == url("invigilator:report", exam)
    assert client.get(join)["Location"] == url("exam", exam)
    other = services.create_exam(name="Other", owner=exam.owner)
    other_join = reverse("join", args=[other.examiner_token])
    assert 'value="Ann Lee"' in client.get(other_join).content.decode()
    client.post(other_join, {"name": "Ann Lee"})
    assert client.get(url("exam", other))["Location"] == url("examiner:clarifications", other)
    assert client.get(url("exam", exam))["Location"] == url("invigilator:report", exam)


def test_unknown_token_and_non_member_get_404(client: Client, exam: Exam) -> None:
    assert client.get(reverse("join", args=["nope"])).status_code == 404
    assert client.get(url("invigilator:report", exam)).status_code == 404


def test_invigilator_link_does_not_downgrade_examiner(examiner: Client, exam: Exam) -> None:
    examiner.get(reverse("join", args=[exam.invigilator_token]))
    assert examiner.get(url("examiner:clarifications", exam)).status_code == 200


def test_examiner_link_promotes_a_invigilator(invigilator: Client, exam: Exam) -> None:
    invigilator.get(reverse("join", args=[exam.examiner_token]))
    assert invigilator.get(url("examiner:clarifications", exam)).status_code == 200


def test_owner_picks_a_name_through_the_examiner_link(
    client: Client, exam: Exam, staff: User
) -> None:
    client.force_login(staff)
    page = url("examiner:clarifications", exam)
    response = client.get(page)
    join = reverse("join", args=[exam.examiner_token])
    assert response["Location"] == f"{join}?{urlencode({'next': page})}"
    assert client.post(response["Location"], {"name": "Lee"})["Location"] == page
    assert client.get(page).status_code == 200
    client.get(reverse("join", args=[exam.invigilator_token]))
    assert client.get(page).status_code == 200


def test_creating_an_exam_requires_staff(client: Client, staff: User) -> None:
    response = client.post(reverse("create_exam"), {"name": "CS1231S Final"})
    assert response.status_code == 302
    assert not Exam.objects.exists()
    client.force_login(staff)
    response = client.post(reverse("create_exam"), {"name": "CS1231S Final"})
    exam = Exam.objects.get()
    assert response["Location"] == url("examiner:settings", exam)
    assert exam.owner == staff
    assert (exam.members.get().name, exam.members.get().role) == ("lee", "examiner")
    assert client.get(response["Location"]).status_code == 200


@pytest.mark.parametrize(
    ("method", "name"),
    [
        *[
            ("get", name)
            for name in (
                "examiner:clarifications",
                "examiner:seats",
                "examiner:settings",
                "examiner:announcements",
            )
        ],
        *[
            ("post", name)
            for name in (
                "examiner:announce",
                "examiner:reset_link",
                "examiner:close_exam",
                "examiner:delete_exam",
            )
        ],
    ],
)
def test_invigilator_cannot_use_examiner_views(
    invigilator: Client, exam: Exam, method: str, name: str
) -> None:
    assert getattr(invigilator, method)(url(name, exam), {"text": "x"}).status_code == 403


def test_only_examiners_answer(invigilator: Client, examiner: Client, exam: Exam) -> None:
    clarification = report(exam).clarification
    data = {"text": "Yes", "expected_answer": ""}
    assert (
        invigilator.post(
            url("examiner:answer_clarification", exam, clarification.pk), data
        ).status_code
        == 403
    )
    assert (
        examiner.post(
            url("examiner:answer_clarification", exam, clarification.pk), data
        ).status_code
        == 302
    )
    assert Clarification.objects.get().current_answer is not None


def test_pages_carry_csrf_header_for_htmx(examiner: Client, exam: Exam) -> None:
    html = examiner.get(url("examiner:clarifications", exam)).content.decode()
    assert re.search(r"<body[^>]*hx-headers:inherited='\{\"X-CSRFToken\": \"\w+\"\}'", html)


def test_polling_returns_204_until_version_changes(
    examiner: Client, exam: Exam, django_assert_max_num_queries: DjangoAssertNumQueries
) -> None:
    report(exam)
    page = examiner.get(url("examiner:clarifications", exam)).content.decode()
    poll = poll_url(page)
    with django_assert_max_num_queries(2):  # the session, and the member with its exam
        assert examiner.get(poll, headers=HTMX).status_code == 204
    report(exam, label="Q7")
    fragment = examiner.get(poll, headers=HTMX)
    assert fragment.status_code == 200
    assert b"<html" not in fragment.content
    assert b"Q7" in fragment.content


def test_closed_exam_stops_invigilator_and_is_read_only(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    clarification = report(exam).clarification
    services.close_exam(exam)
    assert invigilator.get(url("invigilator:report", exam)).status_code == 403
    assert (
        invigilator.get(url("invigilator:to_deliver", exam), headers=HTMX)["HX-Refresh"] == "true"
    )
    assert Client().get(reverse("join", args=[exam.invigilator_token])).status_code == 403
    assert examiner.get(url("examiner:clarifications", exam)).status_code == 200
    assert examiner.get(url("examiner:seats", exam), headers=HTMX).status_code == 200
    examiner.post(url("examiner:answer_clarification", exam, clarification.pk), {"text": "Yes"})
    assert Clarification.objects.get().current_answer is None


def test_deleted_exam_stops_polling_and_shows_the_404_page(examiner: Client, exam: Exam) -> None:
    pk = exam.pk
    services.close_exam(exam)
    services.delete_exam(exam)
    poll = examiner.get(reverse("examiner:clarifications", args=[pk]), headers=HTMX)
    assert poll["HX-Refresh"] == "true"
    page = examiner.get(reverse("examiner:clarifications", args=[pk]))
    assert page.status_code == 404
    assert "Open the exam link you were given." in page.content.decode()


def test_photo_is_served_to_members_only(
    invigilator: Client, exam: Exam, media: Path, jpeg: bytes
) -> None:
    r = report(exam, photo=SimpleUploadedFile("p.jpg", jpeg, "image/jpeg"))
    response = invigilator.get(url("photo", exam, r.pk))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == jpeg  # type: ignore[attr-defined]
    assert Client().get(url("photo", exam, r.pk)).status_code == 404


def test_every_page_and_fragment_renders_with_data(
    invigilator: Client, examiner: Client, exam: Exam, media: Path, jpeg: bytes
) -> None:
    with_venue(invigilator, exam, "LT19")
    answered = report(exam, photo=SimpleUploadedFile("p.jpg", jpeg, "image/jpeg"))
    answer = services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    services.mark_delivered(answered, answer=answer, member=named(exam, "Ann"))
    report(exam, seat="C1", clarification=answered.clarification)
    report(exam, label="Q3a")
    services.announce(text="Typo in Q2", member=named(exam, "Prof"))
    pages = [
        (invigilator, url("invigilator:report", exam) + "?venue=LT19&seat=d4&label=3"),
        (invigilator, url("invigilator:open_clarifications", exam)),
        (invigilator, url("invigilator:to_deliver", exam)),
        (invigilator, url("invigilator:deliver_seat", exam) + "?venue=LT19&seat=C1"),
        (invigilator, url("invigilator:announcements", exam)),
        (invigilator, url("invigilator:tab_bar", exam) + "?tab=report&v="),
        (
            examiner,
            url("examiner:clarifications", exam) + "?state=all&label=Q3b&label=Q5&sort=-seats",
        ),
        (examiner, url("examiner:clarification", exam, answered.clarification_id)),
        (examiner, url("examiner:seats", exam)),
        (examiner, url("examiner:members", exam)),
        (examiner, url("examiner:seat", exam) + "?venue=LT19&seat=B14"),
        (examiner, url("examiner:settings", exam)),
        (examiner, url("examiner:announcements", exam)),
        (examiner, url("examiner:announcements_badge", exam) + "?v="),
        (examiner, url("examiner:clarifications_badge", exam) + "?v="),
    ]
    for client, page in pages:
        for headers in ({}, HTMX):
            response = client.get(page, headers=headers)
            assert response.status_code == 200, page
    matched = invigilator.get(pages[0][1]).content.decode()
    assert '<button class="match">' in matched
    assert "C1" in invigilator.get(url("invigilator:to_deliver", exam)).content.decode()
    assert "<svg" in examiner.get(url("examiner:settings", exam)).content.decode()


@pytest.mark.parametrize(("first_name", "expected"), [("Lee Wei", "Lee Wei"), ("", "lee")])
def test_name_is_prefilled_for_staff(
    client: Client, exam: Exam, staff: User, first_name: str, expected: str
) -> None:
    staff.first_name = first_name
    staff.save()
    client.force_login(staff)
    join = reverse("join", args=[exam.examiner_token])
    assert f'value="{expected}"' in client.get(join).content.decode()


def test_polls_pause_while_a_dialog_is_open(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    guard = "hx-trigger=\"every 2s [!document.querySelector('dialog[open]')]\""
    pages = [
        *[
            (invigilator, page)
            for page in (
                "invigilator:open_clarifications",
                "invigilator:to_deliver",
                "invigilator:announcements",
            )
        ],
        *[
            (examiner, page)
            for page in (
                "examiner:clarifications",
                "examiner:seats",
                "examiner:seat",
                "examiner:announcements",
            )
        ],
    ]
    for client, page in pages:
        assert guard in client.get(url(page, exam)).content.decode(), page
    for client, badges in (
        (invigilator, "invigilator:tab_bar"),
        (examiner, "examiner:announcements_badge"),
        (examiner, "examiner:clarifications_badge"),
    ):
        assert guard in client.get(url(badges, exam) + "?v=", headers=HTMX).content.decode(), badges


def test_leave_takes_the_exam_off_this_device(invigilator: Client, exam: Exam) -> None:
    with_venue(invigilator, exam, "LT19")
    page = invigilator.get(url("invigilator:report", exam)).content.decode()
    assert 'aria-label="Account: ' in page
    assert f'action="{url("leave_exam", exam)}"' in page
    response = invigilator.post(url("leave_exam", exam), follow=True)
    assert response.redirect_chain[-1][0] == reverse("home")
    assert invigilator.get(url("invigilator:report", exam)).status_code == 404
    assert session.member_id(invigilator.session, exam.pk) is None
    assert exam.members.filter(name="Ann").exists()


def test_home_shows_exam_cards_with_counts(client: Client, exam: Exam, staff: User) -> None:
    answered = report(exam)
    services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    report(exam, seat="C1", label="Q5")
    client.force_login(staff)
    html = client.get(reverse("home")).content.decode()
    assert 'commandfor="create-dialog" command="show-modal"><svg' in html
    assert "Create exam</button>" in html
    assert '<span class="live-dot"></span>Live</span>' in html
    assert "<strong>1</strong><span>open clarification</span>" in html
    assert "<strong>1</strong><span>seat to deliver</span>" in html


def test_home_is_a_landing_page_until_there_is_an_exam(
    invigilator: Client, staff: User, settings: Settings
) -> None:
    landing = "examdesk/landing.html"
    visitor = Client()
    response = visitor.get(reverse("home"))
    assert landing in [t.name for t in response.templates]
    assert f'href="{reverse("account_signup")}"' in response.content.decode()
    settings.EXAMDESK_REGISTRATION = False
    assert (
        f'href="{reverse("account_signup")}"' not in visitor.get(reverse("home")).content.decode()
    )
    assert landing not in [t.name for t in invigilator.get(reverse("home")).templates]
    visitor.force_login(staff)
    assert landing not in [t.name for t in visitor.get(reverse("home")).templates]


def test_delivery_state_updates_by_polling(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    first = report(exam)
    second = report(exam, seat="C1", clarification=first.clarification)
    answer = services.answer_clarification(
        first.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    clarification_poll = poll_url(
        examiner.get(url("examiner:clarification", exam, first.clarification_id)).content.decode()
    )
    page = f"{url('invigilator:deliver_seat', exam)}?venue=LT19&seat=B14"
    show_poll = poll_url(invigilator.get(page).content.decode())
    seat_poll = poll_url(
        examiner.get(url("examiner:seat", exam) + "?venue=LT19&seat=B14").content.decode()
    )
    services.mark_delivered(first, answer=answer, member=named(exam, "Bob"))  # another invigilator
    services.mark_delivered(second, answer=answer, member=named(exam, "Bob"))
    clarification_live = examiner.get(clarification_poll, headers=HTMX).content.decode()
    assert clarification_live.count('<span class="badge state-delivered">Delivered</span>') == 2
    show_live = invigilator.get(show_poll, headers=HTMX).content.decode()
    assert "Unmark</button>" in show_live
    assert 'text-base-content">Bob</strong>' in show_live
    seat_live = examiner.get(seat_poll, headers=HTMX).content.decode()
    assert icon_then("check", "Delivered by Bob at") in seat_live
    assert icon_then("pen-line", "Ann</span>", "Reported by") in seat_live
    assert f'<a href="{url("examiner:seats", exam)}"' in seat_live


def test_a_name_another_device_uses_is_confirmed_or_changed(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    invigilator.post(url("invigilator:choose_venue", exam), {"venue": "lt 19"}, follow=True)
    join = reverse("join", args=[exam.invigilator_token])
    phone = Client()
    html = phone.post(join, {"name": " ann "}).content.decode()  # case and spacing aside, Ann
    assert "Ann is already on this exam" in html
    assert "In LT19, online now." in html
    assert phone.get(url("invigilator:report", exam)).status_code == 404
    # That's me: one Ann, on two devices.
    assert phone.post(join, {"name": "Ann", "same": "1"})["Location"] == url("exam", exam)
    assert session.member_id(phone.session, exam.pk) == session.member_id(
        invigilator.session, exam.pk
    )
    assert phone.get(url("invigilator:report", exam)).status_code == 200
    html = examiner.get(url("examiner:members", exam)).content.decode()
    assert html.count(">Online<") == 2  # Ann and Prof
    # A different person on another device picks another name.
    laptop = Client()
    assert "Ann is already on this exam" in laptop.post(join, {"name": "Ann"}).content.decode()
    open_link(laptop, exam.invigilator_token, "Ann Lee")
    assert laptop.get(url("invigilator:report", exam)).status_code == 200
    assert ">Ann Lee<" in examiner.get(url("examiner:members", exam)).content.decode()


def test_a_invigilator_cannot_take_an_examiners_name(examiner: Client, exam: Exam) -> None:
    html = Client().post(reverse("join", args=[exam.invigilator_token]), {"name": "prof"}).content
    assert "An examiner on this exam uses that name." in html.decode()
    assert exam.members.count() == 1


def test_rename_applies_to_the_member_on_this_exam(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    page = url("invigilator:report", exam)
    assert (
        invigilator.post(url("rename_member", exam), {"name": "Ann Tan", "next": page})["Location"]
        == page
    )
    assert exam.members.filter(name="Ann Tan").exists()
    html = invigilator.post(url("rename_member", exam), {"name": "PROF"}).content.decode()
    assert "Someone on this exam already uses that name." in html


def test_reset_link_stops_the_old_link_and_keeps_members(
    invigilator: Client, examiner: Client, exam: Exam
) -> None:
    old = exam.invigilator_token
    response = examiner.post(url("examiner:reset_link", exam), {"role": "invigilator"})
    assert response["Location"] == url("examiner:settings", exam)
    exam.refresh_from_db()
    assert exam.invigilator_token != old
    assert Client().get(reverse("join", args=[old])).status_code == 404
    assert invigilator.get(url("invigilator:report", exam)).status_code == 200
    newcomer = Client()
    open_link(newcomer, exam.invigilator_token, "Ben")
    assert newcomer.get(url("invigilator:report", exam)).status_code == 200
    old = exam.examiner_token
    examiner.post(url("examiner:reset_link", exam), {"role": "examiner"})
    assert Client().get(reverse("join", args=[old])).status_code == 404
    assert examiner.get(url("examiner:clarifications", exam)).status_code == 200


def test_reset_link_needs_a_known_role(examiner: Client, exam: Exam) -> None:
    assert examiner.post(url("examiner:reset_link", exam), {"role": "owner"}).status_code == 400


def test_server_refuses_to_start_without_a_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "examdesk.wsgi", raising=False)
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    with pytest.raises(ImproperlyConfigured):
        importlib.import_module("examdesk.wsgi")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "x" * 50)
    importlib.import_module("examdesk.wsgi")
