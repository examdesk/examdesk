import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from examdesk import services
from examdesk.models import Exam
from tests.helpers import named, report

pytestmark = pytest.mark.django_db


def test_every_examdesk_page_renders(client: Client, exam: Exam, staff: User) -> None:
    prof, ann = named(exam, "Prof"), named(exam, "Ann")
    answered = report(exam)
    answer = services.answer_clarification(
        answered.clarification, text="Yes.", member=prof, expected_answer=None
    )
    services.mark_delivered(answered, answer=answer, member=ann)
    services.claim_seat(ann, venue="LT19", seat="C1")
    announced = report(exam, seat="B15", label="Q4")
    announcement = services.announce(
        prof, text="Q4 is cancelled.", clarification=announced.clarification
    )
    services.mark_read_out(announcement, venue="LT19", member=ann)
    staff.is_superuser = True
    staff.save()
    client.force_login(staff)

    for model in admin.site._registry:
        if model._meta.app_label != "examdesk":
            continue
        prefix = f"admin:examdesk_{model._meta.model_name}"
        obj = model._default_manager.first()
        assert obj is not None, model
        for page in [
            reverse(f"{prefix}_changelist"),
            reverse(f"{prefix}_add"),
            reverse(f"{prefix}_change", args=[obj.pk]),
        ]:
            assert client.get(page).status_code == 200, page
