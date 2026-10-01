"""Signing in with an emailed code, and signing up, through allauth."""

import re

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings

from examdesk.models import Exam

pytestmark = pytest.mark.django_db

CODE = r"^([A-Z]{4}-[A-Z]{4})$"


@pytest.fixture(autouse=True)
def no_rate_limits_carried_over() -> None:
    cache.clear()


def emailed_code() -> str:
    match = re.search(CODE, str(mail.outbox[-1].body), re.MULTILINE)
    assert match, mail.outbox[-1].body
    return match[1]


def request_code(client: Client, email: str, next: str = "") -> str:
    data = {"login": email} | ({"next": next} if next else {})
    response = client.post(reverse("account_login"), data)
    return str(response["Location"])


def test_sign_in_with_an_emailed_code(client: Client, staff: User) -> None:
    response = client.get("/admin/login/?next=/admin/")
    assert response["Location"] == reverse("account_login") + "?next=/admin/"
    page = client.get(response["Location"]).content.decode()
    assert "Staff" not in page
    assert 'name="password"' not in page
    assert request_code(client, "LEE@example.edu", next="/admin/").startswith(
        reverse("account_confirm_login_code")
    )
    assert mail.outbox[-1].to == ["lee@example.edu"]
    assert "http" not in emailed_code()
    response = client.post(reverse("account_confirm_login_code"), {"code": "BBBB-BBBB"})
    assert response.status_code == 200
    response = client.post(
        reverse("account_confirm_login_code"), {"code": emailed_code(), "next": "/admin/"}
    )
    assert response["Location"] == "/admin/"
    assert client.get(reverse("home")).context["user"] == staff
    client.post(reverse("account_logout"))
    assert not client.get(reverse("home")).context["user"].is_authenticated


def test_code_stops_working_after_three_wrong_tries(client: Client, staff: User) -> None:
    request_code(client, staff.email)
    code = emailed_code()
    for _ in range(3):
        client.post(reverse("account_confirm_login_code"), {"code": "BBBB-BBBB"})
    response = client.post(reverse("account_confirm_login_code"), {"code": code})
    assert not client.get(reverse("home")).context["user"].is_authenticated
    assert response.status_code in (200, 302)


def test_unknown_address_gets_no_code(client: Client) -> None:
    location = request_code(client, "nobody@example.edu")
    assert location.startswith(reverse("account_confirm_login_code"))
    assert not re.search(CODE, str(mail.outbox[-1].body), re.MULTILINE)


def test_codes_per_address_are_rate_limited(staff: User) -> None:
    responses = [Client().post(reverse("account_login"), {"login": staff.email}) for _ in range(4)]
    assert responses[0]["Location"].startswith(reverse("account_confirm_login_code"))
    assert "Too many" in responses[-1].content.decode()


def test_sign_up_can_be_closed(client: Client, settings: Settings) -> None:
    settings.EXAMDESK_REGISTRATION = False
    assert reverse("account_signup") not in client.get(reverse("account_login")).content.decode()
    assert "Sign up is closed" in client.get(reverse("account_signup")).content.decode()


def test_sign_up_with_an_allowed_address(client: Client, settings: Settings) -> None:
    settings.EXAMDESK_REGISTRATION_DOMAINS = ["example.edu"]
    page = client.get(reverse("account_login")).content.decode()
    assert reverse("account_signup") in page
    assert "example.edu" in page
    response = client.post(reverse("account_signup"), {"email": "tan@gmail.com", "name": "Tan"})
    assert "Use an address at example.edu." in response.content.decode()
    response = client.post(
        reverse("account_signup"), {"email": "tan@example.edu", "name": "Tan Wei"}
    )
    assert response["Location"] == reverse("account_email_verification_sent")
    response = client.post(response["Location"], {"code": emailed_code()})
    assert response["Location"] == reverse("home")
    user = User.objects.get(email="tan@example.edu")
    assert (user.first_name, user.is_staff, user.has_usable_password()) == ("Tan Wei", False, False)
    client.post(reverse("create_exam"), {"name": "CS1231S Final"})
    exam = Exam.objects.get()
    assert (exam.owner, exam.members.get().name) == (user, "Tan Wei")


def test_creating_an_exam_needs_an_account(client: Client) -> None:
    response = client.post(reverse("create_exam"), {"name": "CS1231S Final"})
    assert response["Location"].startswith(reverse("account_login"))
    assert not Exam.objects.exists()
