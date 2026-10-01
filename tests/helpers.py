"""Shared test helpers; fixtures are in `conftest.py`."""

import re
import uuid
from typing import Any

from django.test import Client
from django.urls import reverse

from examdesk import services, session
from examdesk.models import Clarification, Exam, Member, Report, Role
from examdesk.seats import seat_url
from examdesk.templatetags.examdesk_tags import icon


def named(exam: Exam, name: str) -> Member:
    """The exam's member with that name, made if new: an examiner if it starts with Prof."""
    role = Role.EXAMINER if name.startswith("Prof") else Role.INVIGILATOR
    found = exam.members.filter(name__iexact=name).first()
    return found or services.add_member(exam, name=name, role=role)


def report(exam: Exam, **kwargs: Any) -> Report:
    """Ann reports LT19 B14 as a new Q3b clarification, or joining `clarification`."""
    fields: dict[str, Any] = {
        "client_key": uuid.uuid4(),
        "member": named(exam, "Ann"),
        "venue": "LT19",
        "seat": "B14",
        "label": "Q3b",
        "text": "Is x an integer?",
    }
    return services.add_report(**(fields | kwargs))


def state(report: Report) -> Report.State:
    return Report.objects.with_state().get(pk=report.pk).state


def fresh(clarification: Clarification) -> Clarification:
    clarification.refresh_from_db()
    return clarification


def url(name: str, exam: Exam, *args: object) -> str:
    return reverse(name, args=[exam.pk, *args])


def open_link(client: Client, token: str, name: str = "Ann") -> None:
    response = client.post(reverse("join", args=[token]), {"name": name})
    assert response.status_code == 302


HTMX = {"HX-Request": "true"}


def seat_page(exam: Exam, venue: str, seat: str, report: Report | None = None) -> str:
    return seat_url(
        "invigilator:deliver_seat", exam.pk, venue, seat, report=report.pk if report else ""
    )


def member(client: Client, exam: Exam) -> Member:
    found = session.member(client.session, exam)
    assert found
    return found


def with_venue(client: Client, exam: Exam, venue: str) -> Client:
    services.set_venue(member(client, exam), venue)
    return client


def icon_then(name: str, text: str, title: str = "") -> str:
    """An icon followed by its text, as rendered."""
    return f"{icon(name, title)}{text}"


def poll_url(html: str) -> str:
    found = re.search(r'id="live"[^>]* hx-get="([^"]+)"', html)
    assert found
    return found[1].replace("&amp;", "&")
