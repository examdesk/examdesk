"""The clarifications export."""

import csv
import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from examdesk import services
from examdesk.models import Announcement, Answer, Clarification, Exam
from tests.helpers import member, named, report, url

pytestmark = pytest.mark.django_db


def utc_time(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 26, hour, minute, tzinfo=UTC)


def test_one_row_per_clarification_by_question(examiner: Client, exam: Exam) -> None:
    services.edit_exam(exam, name=exam.name, time_zone=ZoneInfo("Asia/Singapore"))
    prof, ann = member(examiner, exam), named(exam, "Ann")
    answered_report = report(exam, label="Q3b", text="Is x an integer?")
    report(exam, clarification=answered_report.clarification, venue="HALL2", seat="3")
    duplicate_report = report(exam, label="Q3b", seat="15", text="Can x be 1.5?")
    services.merge_clarifications(
        sources=[duplicate_report.clarification], target=answered_report.clarification
    )
    answer = services.answer_clarification(
        answered_report.clarification, text="Yes, an integer.", member=prof, expected_answer=None
    )
    services.mark_delivered(answered_report, answer=answer, member=ann)
    announced_clarification = report(
        exam, label="Q10", seat="7", text="Typo in the graph?"
    ).clarification
    announcement = services.announce(
        prof, text="Read y as z.", clarification=announced_clarification
    )
    services.mark_read_out(announcement, venue="HALL1", member=ann)
    open_report = report(exam, label="Q2", venue="HALL2", seat="1", text="Café, or the bar?")
    clarifications_in_asked_order = [
        answered_report.clarification,
        duplicate_report.clarification,
        announced_clarification,
        open_report.clarification,
    ]
    for hour, clarification in enumerate(clarifications_in_asked_order, start=1):
        Clarification.objects.filter(pk=clarification.pk).update(created_at=utc_time(hour, 0))
    Answer.objects.filter(pk=answer.pk).update(created_at=utc_time(1, 5))
    Announcement.objects.filter(pk=announcement.pk).update(created_at=utc_time(3, 10))

    response = examiner.get(url("examiner:export_clarifications", exam))
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == [
        "Question",
        "Wording",
        "State",
        "Merged into",
        "Answer",
        "Announcement",
        "Seat count",
        "Seats delivered",
        "Seats",
        "Asked at",
        "Answered at",
    ]
    q2, q3b, q3b_merged, q10 = rows[1:]
    assert q2 == [
        "Q2",
        "Café, or the bar?",
        "Open",
        "",
        "",
        "",
        "1",
        "0",
        "HALL2 1",
        "2026-11-26 12:00",
        "",
    ]
    assert q3b == [
        "Q3b",
        "Is x an integer?",
        "Answered",
        "",
        "Yes, an integer.",
        "",
        "3",
        "1",
        "HALL1 14, HALL1 15, HALL2 3",
        "2026-11-26 09:00",
        "2026-11-26 09:05",
    ]
    assert q3b_merged == [
        "Q3b",
        "Can x be 1.5?",
        "Merged",
        "Q3b · Is x an integer?",
        "",
        "",
        "0",
        "0",
        "",
        "2026-11-26 10:00",
        "",
    ]
    assert q10 == [
        "Q10",
        "Typo in the graph?",
        "Announced",
        "",
        "",
        "Read y as z.",
        "1",
        "1",
        "HALL1 7",
        "2026-11-26 11:00",
        "2026-11-26 11:10",
    ]


def test_export_is_a_csv_file_excel_reads_as_utf8(examiner: Client, exam: Exam) -> None:
    report(exam, text="Is ∑ the sum?")
    response = examiner.get(url("examiner:export_clarifications", exam))
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"] == (
        'attachment; filename="Biology Final clarifications.csv"'
    )
    assert response.content.startswith(b"\xef\xbb\xbfQuestion,")
    assert "Is ∑ the sum?" in response.content.decode("utf-8-sig")


def test_examiners_export_closed_exams_from_settings(examiner: Client, exam: Exam) -> None:
    services.set_live(exam, live=False)
    export_url = url("examiner:export_clarifications", exam)
    assert examiner.get(export_url).status_code == 200
    html = examiner.get(url("examiner:settings", exam)).content.decode()
    assert f'href="{export_url}" download' in html


@pytest.mark.parametrize("formula_prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_cells_that_look_like_formulas_stay_text(
    examiner: Client, exam: Exam, formula_prefix: str
) -> None:
    clarification = report(exam).clarification
    services.answer_clarification(
        clarification, text="-1", member=member(examiner, exam), expected_answer=None
    )
    # Set directly: reports strip a leading tab or carriage return.
    Clarification.objects.filter(pk=clarification.pk).update(wording=f"{formula_prefix}x or x?")
    response = examiner.get(url("examiner:export_clarifications", exam))
    [row] = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"), newline="")))
    assert (row["Wording"], row["Answer"]) == (f"'{formula_prefix}x or x?", "'-1")
