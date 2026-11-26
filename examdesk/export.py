"""An exam's clarifications as CSV."""

import csv
import io
from datetime import datetime

from django.db.models import Prefetch
from django.utils.timezone import localtime

from .labels import natural, place_order
from .models import SEAT_STATES, Exam, Report, SeatState
from .templatetags.examdesk_tags import titled


def escape_formula(cell: str) -> str:
    """The cell with a ' in front if spreadsheets would read it as a formula, as OWASP advises."""
    return f"'{cell}" if cell.startswith(("=", "+", "-", "@", "\t", "\r")) else cell


def clarifications_csv(exam: Exam) -> bytes:
    """One row per clarification, by question, with times in the exam's time zone."""
    latest_announcements = {
        announcement.clarification_id: announcement
        for announcement in exam.announcements.exclude(clarification=None).order_by("created_at")
    }

    def local_time(value: datetime) -> str:
        return localtime(value, exam.time_zone).strftime("%Y-%m-%d %H:%M")

    clarifications = exam.clarifications.select_related(
        "current_answer", "merged_into"
    ).prefetch_related(Prefetch("reports", Report.objects.with_state()))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
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
    )
    for clarification in sorted(
        clarifications, key=lambda clarification: natural(clarification.label)
    ):
        reports = sorted(
            clarification.reports.all(), key=lambda report: place_order((report.venue, report.seat))
        )
        answer = clarification.current_answer
        announcement = latest_announcements.get(clarification.pk)
        answer_or_announcement = answer or announcement
        row = [
            clarification.label,
            clarification.summary,
            clarification.state.label,
            titled(clarification.merged_into) if clarification.merged_into else "",
            answer.text if answer else "",
            announcement.text if announcement else "",
            str(len(reports)),
            str(sum(SEAT_STATES[report.state] == SeatState.DELIVERED for report in reports)),
            ", ".join(str(report) for report in reports),
            local_time(clarification.created_at),
            local_time(answer_or_announcement.created_at) if answer_or_announcement else "",
        ]
        writer.writerow([escape_formula(cell) for cell in row])
    # With a byte order mark, Excel reads the file as UTF-8.
    return buffer.getvalue().encode("utf-8-sig")
