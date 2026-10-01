import uuid
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

from examdesk import services
from examdesk.models import Answer, Clarification, Delivery, Exam, Readout, Report
from examdesk.services import AnswerConflict, ServiceError
from tests.helpers import fresh, named, report, state

pytestmark = pytest.mark.django_db
S = Report.State


def version(exam: Exam) -> int:
    exam.refresh_from_db()
    return exam.version


def test_create_exam_makes_distinct_tokens(exam: Exam) -> None:
    assert exam.is_live
    assert len(exam.invigilator_token) >= 32
    assert exam.invigilator_token != exam.examiner_token


def test_new_report_creates_a_clarification(exam: Exam) -> None:
    r = report(exam)
    assert r.clarification.label == "Q3b"
    assert r.clarification.wording == "Is x an integer?"
    assert state(r) == S.OPEN
    assert version(exam) == 1


def test_new_clarification_needs_text_or_photo(exam: Exam) -> None:
    with pytest.raises(ServiceError):
        report(exam, text=" ")


def test_retry_with_same_client_key_does_not_duplicate(exam: Exam) -> None:
    key = uuid.uuid4()
    first = report(exam, client_key=key)
    again = report(exam, client_key=key)
    assert again == first
    assert Report.objects.count() == Clarification.objects.count() == 1


def test_client_key_of_another_exam_is_refused(exam: Exam) -> None:
    key = uuid.uuid4()
    report(exam, client_key=key)
    other = services.create_exam(name="Other", owner=exam.owner)
    with pytest.raises(ServiceError, match="another exam"):
        report(other, client_key=key)


def test_same_question_joins_open_clarification(exam: Exam) -> None:
    first = report(exam)
    second = report(exam, seat="C1", clarification=first.clarification, text="")
    assert second.clarification == first.clarification
    assert state(second) == S.OPEN


def test_cannot_join_closed_clarification(exam: Exam) -> None:
    first = report(exam)
    services.announce(text="!", member=named(exam, "Prof"), clarification=first.clarification)
    with pytest.raises(ServiceError):
        report(exam, clarification=first.clarification)


def test_photo_is_stored_under_exam_folder(exam: Exam, media: Path, jpeg: bytes) -> None:
    r = report(exam, text="", photo=SimpleUploadedFile("p.jpg", jpeg, "image/jpeg"))
    name = r.photo.name or ""
    assert name.startswith(f"exams/{exam.pk}/")
    assert (media / name).exists()


def test_answer_moves_seats_to_deliver(exam: Exam) -> None:
    r = report(exam)
    services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    clarification = fresh(r.clarification)
    assert clarification.state == Clarification.State.ANSWERED
    assert state(r) == S.TO_DELIVER


def test_correction_requeues_delivered_seats_and_keeps_history(exam: Exam) -> None:
    r = report(exam)
    old = services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    services.mark_delivered(r, answer=old, member=named(exam, "Ann"))
    assert state(r) == S.DELIVERED
    new = services.answer_clarification(
        r.clarification, text="Yes, positive", member=named(exam, "Prof"), expected_answer=old.pk
    )
    assert state(r) == S.TO_DELIVER
    assert list(fresh(r.clarification).answers.all()) == [new, old]
    assert r.deliveries.get().answer == old


def test_concurrent_answer_race_fails_with_other_answer(exam: Exam) -> None:
    r = report(exam)
    # Both examiners opened the clarification while it was unanswered.
    winner = services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    before = version(exam)
    with pytest.raises(AnswerConflict) as conflict:
        services.answer_clarification(
            r.clarification, text="No", member=named(exam, "Tan"), expected_answer=None
        )
    assert conflict.value.answer == winner
    assert fresh(r.clarification).current_answer == winner
    assert r.clarification.answers.count() == 1
    assert version(exam) == before


def test_empty_answer_is_rejected(exam: Exam) -> None:
    with pytest.raises(ServiceError):
        services.answer_clarification(
            report(exam).clarification, text="  ", member=named(exam, "Prof"), expected_answer=None
        )


def test_mark_delivered_is_idempotent_and_unmark_deletes(exam: Exam) -> None:
    r = report(exam)
    answer = services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    services.mark_delivered(r, answer=answer, member=named(exam, "Ann"))
    services.mark_delivered(r, answer=answer, member=named(exam, "Bob"))
    delivery = Delivery.objects.get()
    assert delivery.member.name == "Ann"
    services.unmark_delivered(delivery)
    assert state(r) == S.TO_DELIVER


def test_deliver_rejects_stale_or_missing_answer(exam: Exam) -> None:
    r = report(exam)
    with pytest.raises(ServiceError):
        services.mark_delivered(r, answer=None, member=named(exam, "Ann"))
    old = services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    services.answer_clarification(
        r.clarification, text="No", member=named(exam, "Prof"), expected_answer=old.pk
    )
    with pytest.raises(ServiceError, match="changed"):
        services.mark_delivered(r, answer=old, member=named(exam, "Ann"))


def test_announced_seats_wait_until_read_out_in_their_venue(exam: Exam) -> None:
    here = report(exam)
    there = report(exam, venue="LT20", seat="A1", clarification=here.clarification)
    announcement = services.announce(
        text="!", member=named(exam, "Prof"), clarification=here.clarification
    )
    assert announcement.clarification == here.clarification
    assert (state(here), state(there)) == (S.TO_READ_OUT, S.TO_READ_OUT)
    services.mark_read_out(announcement, venue="LT19", member=named(exam, "Ann"))
    assert (state(here), state(there)) == (S.READ_OUT, S.TO_READ_OUT)


def test_merge_moves_seats_and_closes_source(exam: Exam) -> None:
    a, b = report(exam), report(exam, seat="C1")
    services.merge_clarifications(source=a.clarification, target=b.clarification)
    source = fresh(a.clarification)
    assert source.closed_reason == Clarification.ClosedReason.MERGED
    assert source.merged_into == b.clarification
    assert set(b.clarification.reports.all()) == {a, b}


def test_merge_keeps_the_answered_clarification_as_target(exam: Exam) -> None:
    open_, answered = report(exam), report(exam, seat="C1")
    services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    target = services.merge_clarifications(
        source=answered.clarification, target=open_.clarification
    )
    assert target == answered.clarification
    assert fresh(open_.clarification).closed_reason == Clarification.ClosedReason.MERGED
    assert state(open_) == S.TO_DELIVER


def test_merge_requeues_seats_that_got_a_different_answer(exam: Exam) -> None:
    a, b = report(exam), report(exam, seat="C1")
    ans_a = services.answer_clarification(
        a.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    ans_b = services.answer_clarification(
        b.clarification, text="Yes, as stated", member=named(exam, "Prof"), expected_answer=None
    )
    services.mark_delivered(a, answer=ans_a, member=named(exam, "Ann"))
    services.mark_delivered(b, answer=ans_b, member=named(exam, "Ann"))
    services.merge_clarifications(source=a.clarification, target=b.clarification)
    assert (state(a), state(b)) == (S.TO_DELIVER, S.DELIVERED)


def test_merge_into_a_photo_keeps_the_sources_wording(exam: Exam) -> None:
    photo, text = report(exam), report(exam, seat="C1", text="Is S finite?")
    services.reword_clarification(photo.clarification, "")  # as a photo-only clarification has none
    services.merge_clarifications(source=text.clarification, target=photo.clarification)
    assert fresh(photo.clarification).wording == "Is S finite?"


def test_merge_rejects_a_merged_or_the_same_clarification(exam: Exam) -> None:
    a, b, c = report(exam), report(exam, seat="C1"), report(exam, seat="D4")
    services.merge_clarifications(source=b.clarification, target=c.clarification)
    for target in (b.clarification, a.clarification):
        with pytest.raises(ServiceError):
            services.merge_clarifications(source=a.clarification, target=target)


def test_merge_into_an_announced_clarification(exam: Exam) -> None:
    announced, waiting, answered = report(exam), report(exam, seat="C1"), report(exam, seat="D4")
    services.announce(text="!", member=named(exam, "Prof"), clarification=announced.clarification)
    services.answer_clarification(
        answered.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    with pytest.raises(ServiceError, match="answered"):
        services.merge_clarifications(source=answered.clarification, target=announced.clarification)
    services.merge_clarifications(source=waiting.clarification, target=announced.clarification)
    assert state(Report.objects.get(pk=waiting.pk)) == S.TO_READ_OUT


def test_move_report_to_new_or_existing_clarification(exam: Exam) -> None:
    a = report(exam)
    b = report(exam, seat="C1", clarification=a.clarification, text="Is x positive?")
    new = services.move_report(b, target=None)
    assert new != a.clarification
    assert (new.label, new.wording) == (a.clarification.label, "Is x positive?")
    back = services.move_report(b, target=a.clarification)
    assert back == a.clarification
    assert set(a.clarification.reports.all()) == {a, b}


def test_announce_with_clarification_closes_it(exam: Exam) -> None:
    r = report(exam)
    a = services.announce(
        text="Typo: x is y", member=named(exam, "Prof"), clarification=r.clarification
    )
    assert a.label == r.clarification.label
    assert fresh(r.clarification).closed_reason == Clarification.ClosedReason.ANNOUNCED
    assert state(r) == S.TO_READ_OUT


def test_announce_without_clarification_and_ack_per_venue_is_idempotent(exam: Exam) -> None:
    a = services.announce(text="30 minutes left", member=named(exam, "Prof"))
    assert a.label == ""
    services.mark_read_out(a, venue="LT19", member=named(exam, "Ann"))
    services.mark_read_out(a, venue="LT19", member=named(exam, "Bob"))
    services.mark_read_out(a, venue="LT20", member=named(exam, "Cy"))
    assert [(k.venue, k.member.name) for k in Readout.objects.all()] == [
        ("LT19", "Ann"),
        ("LT20", "Cy"),
    ]


def test_relabel_moves_one_clarification_and_back(exam: Exam) -> None:
    a, b = report(exam, label="Q3b"), report(exam, label="Q3b", text="Other?")
    services.relabel_clarification(a.clarification, "Q3c")
    assert (fresh(a.clarification).label, fresh(b.clarification).label) == ("Q3c", "Q3b")
    services.relabel_clarification(a.clarification, "Q3b")
    assert fresh(a.clarification).label == "Q3b"


def test_relabel_keeps_the_announcement_label(exam: Exam) -> None:
    announced = report(exam, label="Q2")
    a = services.announce(
        text="!", member=named(exam, "Prof"), clarification=announced.clarification
    )
    services.relabel_clarification(announced.clarification, "Q9")
    a.refresh_from_db()
    assert a.label == "Q2"


def test_withdraw_answer_restores_the_one_before(exam: Exam) -> None:
    r = report(exam)
    first = services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    services.mark_delivered(r, answer=first, member=named(exam, "Ann"))
    second = services.answer_clarification(
        r.clarification, text="No", member=named(exam, "Prof"), expected_answer=first.pk
    )
    with pytest.raises(ServiceError, match="changed"):
        services.withdraw_answer(r.clarification, expected_answer=first.pk)
    services.withdraw_answer(r.clarification, expected_answer=second.pk)
    assert fresh(r.clarification).current_answer == first
    assert state(r) == S.DELIVERED  # its delivery counts again
    with pytest.raises(ServiceError, match="delivered to 1 seat"):
        services.withdraw_answer(r.clarification, expected_answer=first.pk)


def test_withdrawing_the_only_answer_reopens(exam: Exam) -> None:
    r = report(exam)
    answer = services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    services.withdraw_answer(r.clarification, expected_answer=answer.pk)
    assert state(r) == S.OPEN
    assert not Answer.objects.exists()


@pytest.mark.parametrize("reason", ["announced", "merged"])
def test_reopen_clarification(exam: Exam, reason: str) -> None:
    a, b = report(exam), report(exam, seat="B15", text="Other?")
    if reason == "announced":
        services.announce(text="!", member=named(exam, "Prof"), clarification=a.clarification)
    else:
        services.merge_clarifications(source=a.clarification, target=b.clarification)
    services.reopen_clarification(a.clarification)
    reopened = fresh(a.clarification)
    assert (reopened.closed_reason, reopened.merged_into) == ("", None)
    with pytest.raises(ServiceError, match="open already"):
        services.reopen_clarification(a.clarification)


def test_merge_is_reversed_by_reopening_and_moving_back(exam: Exam) -> None:
    a, b = report(exam), report(exam, seat="B15", text="Other?")
    services.merge_clarifications(source=a.clarification, target=b.clarification)
    services.reopen_clarification(a.clarification)
    services.move_report(Report.objects.get(pk=a.pk), target=a.clarification)
    assert Report.objects.get(pk=a.pk).clarification == a.clarification
    assert Report.objects.get(pk=b.pk).clarification == b.clarification


def test_delete_report_drops_an_unused_clarification(exam: Exam) -> None:
    alone, first = report(exam, label="Q1"), report(exam, label="Q2")
    joined = report(exam, seat="B15", clarification=first.clarification)
    services.delete_report(joined)
    assert list(first.clarification.reports.all()) == [first]
    services.delete_report(alone)
    assert not Clarification.objects.filter(pk=alone.clarification_id).exists()


def test_delete_report_keeps_an_answered_clarification(exam: Exam) -> None:
    r = report(exam)
    services.answer_clarification(
        r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
    )
    services.delete_report(r)
    assert Clarification.objects.filter(pk=r.clarification_id).exists()


def test_actions_bump_version(exam: Exam) -> None:
    r = report(exam)
    before = version(exam)
    services.reword_clarification(r.clarification, "Is x an int?")
    assert version(exam) == before + 1


def test_closed_exam_is_read_only_and_can_reopen(exam: Exam) -> None:
    r = report(exam)
    services.close_exam(exam)
    actions: list[Any] = [
        lambda: report(exam, seat="C1"),
        lambda: services.answer_clarification(
            r.clarification, text="Yes", member=named(exam, "Prof"), expected_answer=None
        ),
        lambda: services.announce(text="Hi", member=named(exam, "Prof")),
        lambda: services.reword_clarification(r.clarification, "Is x an int?"),
    ]
    for action in actions:
        with pytest.raises(ServiceError, match="closed"):
            action()
    services.reopen_exam(exam)
    report(exam, seat="C1")


def test_delete_exam_needs_closed_and_removes_photo_folder(
    exam: Exam,
    media: Path,
    jpeg: bytes,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    report(exam, photo=SimpleUploadedFile("p.jpg", jpeg, "image/jpeg"))
    with pytest.raises(ServiceError):
        services.delete_exam(exam)
    services.close_exam(exam)
    with django_capture_on_commit_callbacks(execute=True):
        services.delete_exam(exam)
    assert not Exam.objects.exists()
    assert not Report.objects.exists()
    assert not (media / "exams" / str(exam.pk)).exists()


def test_a_seat_joining_a_clarification_twice_gets_its_existing_report(exam: Exam) -> None:
    first = report(exam)
    again = report(exam, clarification=first.clarification, text="")
    assert again == first
    assert Report.objects.count() == 1
    parallel = report(exam, text="Is y an integer?")  # a second Q3b clarification: allowed
    assert parallel.clarification != first.clarification


def test_moving_the_last_seat_closes_the_source_as_merged(exam: Exam) -> None:
    a, b = report(exam), report(exam, seat="C1")
    source = a.clarification
    assert services.move_report(a, target=b.clarification) == b.clarification
    source = fresh(source)
    assert (source.closed_reason, source.merged_into) == (
        Clarification.ClosedReason.MERGED,
        b.clarification,
    )


def test_moving_one_of_several_seats_keeps_the_source_open(exam: Exam) -> None:
    a = report(exam)
    report(exam, seat="C1", clarification=a.clarification)
    services.move_report(a, target=None)
    assert fresh(a.clarification).closed_reason == ""
