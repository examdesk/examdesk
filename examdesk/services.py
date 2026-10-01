import shutil
import uuid
from functools import partial
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import (
    Announcement,
    Answer,
    Clarification,
    Delivery,
    Exam,
    Member,
    Readout,
    Report,
    Role,
    SeatClaim,
    new_token,
)


class ServiceError(Exception):
    """Shown to the user as is."""


class AnswerConflict(ServiceError):
    def __init__(self, answer: Answer | None) -> None:
        self.answer = answer
        what = f"{answer.member.name} answered" if answer else "The answer was withdrawn"
        super().__init__(f"{what} in the meantime. Send again to replace it.")


def _begin_write(exam_id: int) -> None:
    """Bump the exam's version, locking its row until commit. Fails on a closed exam."""
    if not Exam.objects.filter(pk=exam_id, is_live=True).update(version=F("version") + 1):
        raise ServiceError("The exam is closed.")


def _unclosed(clarification: Clarification) -> Clarification:
    current = Clarification.objects.get(pk=clarification.pk)
    if current.closed_reason:
        raise ServiceError(f"That clarification is closed ({current.closed_reason}).")
    return current


def _close(
    clarification: Clarification,
    reason: Clarification.ClosedReason,
    merged_into: Clarification | None = None,
) -> None:
    clarification.closed_reason, clarification.merged_into = reason, merged_into
    clarification.save(update_fields=["closed_reason", "merged_into"])


def create_exam(*, name: str, owner: User) -> Exam:
    return Exam.objects.create(name=name, owner=owner)


@transaction.atomic
def add_report(
    member: Member,
    *,
    client_key: uuid.UUID,
    venue: str,
    seat: str,
    label: str = "",
    text: str = "",
    photo: UploadedFile[bytes] | None = None,
    clarification: Clarification | None = None,
) -> Report:
    """Create a clarification for the seat, or add the seat to `clarification`.

    Idempotent on `client_key`, and on a seat joining a clarification it is already on.
    """
    exam = member.exam
    _begin_write(exam.pk)
    if existing := Report.objects.filter(client_key=client_key).first():
        if not Report.objects.of_exam(exam).filter(pk=existing.pk).exists():
            raise ServiceError("That report belongs to another exam.")
        return existing
    text = text.strip()
    if clarification is None:
        if not (text or photo):
            raise ServiceError("Describe the clarification with text or a photo.")
        clarification = Clarification.objects.create(exam=exam, label=label, wording=text)
    else:
        clarification = _unclosed(clarification)
        if asked := clarification.reports.filter(venue=venue, seat=seat).first():
            return asked
    return Report.objects.create(
        clarification=clarification,
        venue=venue,
        seat=seat,
        text=text,
        photo=photo,
        member=member,
        client_key=client_key,
    )


@transaction.atomic
def move_report(report: Report, *, target: Clarification | None) -> Clarification:
    """Move a seat to `target`, or to a new clarification. An emptied source is merged."""
    clarification = report.clarification
    _begin_write(clarification.exam_id)
    if target is None:
        target = Clarification.objects.create(
            exam_id=clarification.exam_id, label=clarification.label, wording=report.text
        )
    else:
        target = _unclosed(target)
    report.clarification = target
    report.save(update_fields=["clarification"])
    if not clarification.reports.exists():
        _close(clarification, Clarification.ClosedReason.MERGED, merged_into=target)
    return target


@transaction.atomic
def answer_clarification(
    clarification: Clarification, *, text: str, member: Member, expected_answer: int | None
) -> Answer:
    """Set a new answer, unless someone else answered since `expected_answer` was read."""
    text = text.strip()
    if not text:
        raise ServiceError("The answer is empty.")
    _begin_write(clarification.exam_id)
    _unclosed(clarification)
    answer = Answer.objects.create(clarification=clarification, text=text, member=member)
    unchanged = (
        Q(current_answer__isnull=True)
        if expected_answer is None
        else Q(current_answer_id=expected_answer)
    )
    updated = Clarification.objects.filter(unchanged, pk=clarification.pk)
    if not updated.update(current_answer=answer):
        current = Clarification.objects.select_related("current_answer__member").get(
            pk=clarification.pk
        )
        raise AnswerConflict(current.current_answer)
    return answer


@transaction.atomic
def merge_clarifications(*, source: Clarification, target: Clarification) -> Clarification:
    """Move the source's seats to the target and close the source.

    The answered one of the two becomes the target, and a target without wording takes
    the source's. Returns the target.
    """
    _begin_write(source.exam_id)
    source, target = _unclosed(source), Clarification.objects.get(pk=target.pk)
    if source.pk == target.pk:
        raise ServiceError("A clarification cannot be merged into itself.")
    if target.closed_reason == Clarification.ClosedReason.MERGED:
        raise ServiceError("That clarification was merged into another.")
    if target.closed_reason and source.current_answer_id:
        raise ServiceError("An answered clarification cannot be merged into an announced one.")
    if source.current_answer_id and not target.current_answer_id:
        source, target = target, source
    if not target.wording and source.wording:
        target.wording = source.wording
        target.save(update_fields=["wording"])
    source.reports.update(clarification=target)
    _close(source, Clarification.ClosedReason.MERGED, merged_into=target)
    return target


@transaction.atomic
def reword_clarification(clarification: Clarification, wording: str) -> None:
    _begin_write(clarification.exam_id)
    Clarification.objects.filter(pk=clarification.pk).update(wording=wording.strip())


@transaction.atomic
def mark_delivered(report: Report, *, answer: Answer | None, member: Member) -> Delivery:
    """Record that the invigilator showed `answer` to the seat. Idempotent."""
    clarification = Clarification.objects.get(pk=report.clarification_id)
    _begin_write(clarification.exam_id)
    if clarification.closed_reason:
        raise ServiceError("That clarification is closed.")
    if answer is None or answer.pk != clarification.current_answer_id:
        raise ServiceError("The answer changed. Show the new one.")
    delivery, _ = Delivery.objects.get_or_create(
        report=report, answer=answer, defaults={"member": member}
    )
    return delivery


def add_member(exam: Exam, *, name: str, role: Role) -> Member:
    return Member.objects.create(exam=exam, name=name, role=role)


def promote(member: Member, role: Role) -> None:
    """Raise an invigilator to examiner; never the reverse."""
    if role == Role.EXAMINER and not member.is_examiner:
        member.role = role
        member.save(update_fields=["role"])


def set_venue(member: Member, venue: str) -> None:
    member.venue = venue
    member.save(update_fields=["venue"])


def remove_member(member: Member) -> None:
    """Their devices lose access; what they did stays. Rejoining with their name restores them."""
    SeatClaim.objects.filter(member=member).delete()
    member.removed = True
    member.save(update_fields=["removed"])


def restore_member(member: Member) -> None:
    member.removed = False
    member.save(update_fields=["removed"])


def rename_member(member: Member, name: str) -> None:
    if member.exam.members.exclude(pk=member.pk).filter(name__iexact=name).exists():
        raise ServiceError("Someone on this exam already uses that name.")
    member.name = name
    member.save(update_fields=["name"])


@transaction.atomic
def claim_seat(member: Member, *, venue: str, seat: str) -> None:
    """Claim the seat for the member, releasing their previous one."""
    _begin_write(member.exam_id)
    SeatClaim.objects.filter(member=member).exclude(venue=venue, seat=seat).delete()
    SeatClaim.objects.update_or_create(
        exam_id=member.exam_id,
        venue=venue,
        seat=seat,
        defaults={"member": member, "claimed_at": timezone.now()},
    )


@transaction.atomic
def release_seat(member: Member) -> None:
    if SeatClaim.objects.filter(member=member).delete()[0]:
        _begin_write(member.exam_id)


@transaction.atomic
def unmark_delivered(delivery: Delivery) -> None:
    _begin_write(delivery.report.clarification.exam_id)
    delivery.delete()


@transaction.atomic
def announce(
    member: Member, *, text: str, clarification: Clarification | None = None
) -> Announcement:
    """Announce to every venue, closing `clarification` if given."""
    text = text.strip()
    if not text:
        raise ServiceError("The announcement is empty.")
    _begin_write(member.exam_id)
    label = ""
    if clarification is not None:
        clarification = _unclosed(clarification)
        if clarification.current_answer_id:
            raise ServiceError("Only an open clarification can be announced.")
        _close(clarification, Clarification.ClosedReason.ANNOUNCED)
        label = clarification.label
    return Announcement.objects.create(
        exam_id=member.exam_id,
        label=label,
        clarification=clarification,
        text=text,
        member=member,
    )


@transaction.atomic
def delete_announcement(announcement: Announcement) -> None:
    _begin_write(announcement.exam_id)
    announcement.delete()


@transaction.atomic
def mark_read_out(announcement: Announcement, *, venue: str, member: Member) -> Readout:
    _begin_write(announcement.exam_id)
    readout, _ = Readout.objects.get_or_create(
        announcement=announcement, venue=venue, defaults={"member": member}
    )
    return readout


@transaction.atomic
def unmark_read_out(readout: Readout) -> None:
    _begin_write(readout.announcement.exam_id)
    readout.delete()


@transaction.atomic
def relabel_clarification(clarification: Clarification, label: str) -> None:
    _begin_write(clarification.exam_id)
    Clarification.objects.filter(pk=clarification.pk).update(label=label)


def _set_live(exam: Exam, *, live: bool) -> None:
    Exam.objects.filter(pk=exam.pk).update(is_live=live, version=F("version") + 1)
    exam.is_live = live


def close_exam(exam: Exam) -> None:
    _set_live(exam, live=False)


def reopen_exam(exam: Exam) -> None:
    _set_live(exam, live=True)


def reset_link(exam: Exam, role: Role) -> None:
    """Replace the role's link. Members who joined with the old one stay."""
    field = Exam.token_field(role)
    token = new_token()
    Exam.objects.filter(pk=exam.pk).update(**{field: token}, version=F("version") + 1)
    setattr(exam, field, token)


@transaction.atomic
def delete_exam(exam: Exam) -> None:
    """Delete a closed exam, and its photos after commit."""
    if Exam.objects.get(pk=exam.pk).is_live:
        raise ServiceError("Close the exam before deleting it.")
    folder = Path(settings.MEDIA_ROOT) / "exams" / str(exam.pk)
    exam.delete()
    transaction.on_commit(partial(shutil.rmtree, folder, ignore_errors=True))


@transaction.atomic
def delete_report(report: Report) -> None:
    """Delete a report and its photo, and its clarification if nothing else uses it."""
    clarification = report.clarification
    _begin_write(clarification.exam_id)
    photo = report.photo.name
    report.delete()
    if photo:
        transaction.on_commit(partial(report.photo.storage.delete, photo))
    if not (clarification.reports.exists() or clarification.answers.exists()):
        clarification.delete()


@transaction.atomic
def withdraw_answer(clarification: Clarification, *, expected_answer: int) -> None:
    """Revert to the previous answer, if `expected_answer` is current and was never delivered."""
    _begin_write(clarification.exam_id)
    current = _unclosed(clarification)
    if current.current_answer_id != expected_answer:
        raise ServiceError("The answer changed in the meantime.")
    answer = Answer.objects.get(pk=expected_answer)
    if delivered := answer.deliveries.count():
        seats = f"{delivered} seat{'s' * (delivered > 1)}"
        raise ServiceError(f"It was delivered to {seats}. Send a correction instead.")
    previous = current.answers.exclude(pk=expected_answer).first()
    Clarification.objects.filter(pk=current.pk).update(current_answer=previous)
    answer.delete()


@transaction.atomic
def reopen_clarification(clarification: Clarification) -> None:
    _begin_write(clarification.exam_id)
    current = Clarification.objects.get(pk=clarification.pk)
    if not current.closed_reason:
        raise ServiceError("That clarification is open already.")
    current.closed_reason, current.merged_into = "", None
    current.save(update_fields=["closed_reason", "merged_into"])
