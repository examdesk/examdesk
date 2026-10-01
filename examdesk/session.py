"""Per exam, which member this device is, its clarification filters and put-off alerts."""

import time

from django.contrib.sessions.backends.base import SessionBase

from .models import Exam, Member

_MEMBERS = "members"
_FILTERS = "filters"
_SNOOZED = "snoozed"


def _members(session: SessionBase) -> dict[str, int]:
    members: dict[str, int] = session.get(_MEMBERS, {})
    return members


def joined_member_ids(session: SessionBase) -> list[int]:
    return list(_members(session).values())


def member_id(session: SessionBase, exam_id: int) -> int | None:
    return _members(session).get(str(exam_id))


def member(session: SessionBase, exam: Exam) -> Member | None:
    pk = member_id(session, exam.pk)
    return exam.members.active().filter(pk=pk).first() if pk else None


def remember(session: SessionBase, member: Member) -> None:
    session[_MEMBERS] = _members(session) | {str(member.exam_id): member.pk}


def forget(session: SessionBase, exam: Exam) -> None:
    for key in (_MEMBERS, _FILTERS, _SNOOZED):
        session[key] = {k: v for k, v in session.get(key, {}).items() if k != str(exam.pk)}


def filters(session: SessionBase, exam: Exam) -> str:
    """The query string of the clarification list last opened on this device."""
    return str(session.get(_FILTERS, {}).get(str(exam.pk), ""))


def save_filters(session: SessionBase, exam: Exam, query: str) -> None:
    session[_FILTERS] = session.get(_FILTERS, {}) | {str(exam.pk): query}


def snoozed(session: SessionBase, exam: Exam) -> dict[int, float]:
    """Announcements whose alert this device put off, and when (`time.time()`) it returns."""
    return {int(k): v for k, v in session.get(_SNOOZED, {}).get(str(exam.pk), {}).items()}


def snooze(session: SessionBase, exam: Exam, announcement_id: int, until: float) -> None:
    pending = {str(k): v for k, v in snoozed(session, exam).items() if v > time.time()}
    pending[str(announcement_id)] = until
    session[_SNOOZED] = session.get(_SNOOZED, {}) | {str(exam.pk): pending}
