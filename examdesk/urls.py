from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path
from django.views.generic import RedirectView

from .views import examiner, exams, invigilator

invigilator_patterns = [
    path("tab-bar/", invigilator.tab_bar, name="tab_bar"),
    path("venue/", invigilator.choose_venue, name="choose_venue"),
    path("report/", invigilator.report, name="report"),
    path("reports/<int:report_id>/", invigilator.report_saved, name="report_saved"),
    path("open/", invigilator.open_clarifications, name="open_clarifications"),
    path("deliver/", invigilator.to_deliver, name="to_deliver"),
    path("deliver/seat/", invigilator.deliver_seat, name="deliver_seat"),
    path("reports/<int:report_id>/delivered/", invigilator.mark_delivered, name="mark_delivered"),
    path(
        "deliveries/<int:delivery_id>/unmark/",
        invigilator.unmark_delivered,
        name="unmark_delivered",
    ),
    path("announcements/", invigilator.announcements, name="announcements"),
    path(
        "announcements/<int:announcement_id>/read-out/",
        invigilator.mark_read_out,
        name="mark_read_out",
    ),
    path(
        "announcements/<int:announcement_id>/snooze/",
        invigilator.snooze_announcement,
        name="snooze_announcement",
    ),
    path(
        "announcements/<int:announcement_id>/unmark-read-out/",
        invigilator.unmark_read_out,
        name="unmark_read_out",
    ),
]

clarification_patterns = [
    path("", examiner.clarification, name="clarification"),
    path("answer/", examiner.answer_clarification, name="answer_clarification"),
    path("withdraw/", examiner.withdraw_answer, name="withdraw_answer"),
    path("announce/", examiner.announce_clarification, name="announce_clarification"),
    path("reopen/", examiner.reopen_clarification, name="reopen_clarification"),
    path("merge/", examiner.merge_clarifications, name="merge_clarifications"),
    path("reword/", examiner.reword_clarification, name="reword_clarification"),
    path("relabel/", examiner.relabel_clarification, name="relabel_clarification"),
]

examiner_patterns: list[URLPattern | URLResolver] = [
    path("clarifications/", examiner.clarifications, name="clarifications"),
    path("clarifications/badge/", examiner.clarifications_badge, name="clarifications_badge"),
    path("clarifications/<int:clarification_id>/", include(clarification_patterns)),
    path("reports/<int:report_id>/move/", examiner.move_report, name="move_report"),
    path("announcements/", examiner.announcements, name="announcements"),
    path("announcements/badge/", examiner.announcements_badge, name="announcements_badge"),
    path("announcements/new/", examiner.announce, name="announce"),
    path(
        "announcements/<int:announcement_id>/delete/",
        examiner.delete_announcement,
        name="delete_announcement",
    ),
    path("seats/", examiner.seats, name="seats"),
    path("seats/seat/", examiner.seat, name="seat"),
    path("members/", examiner.members, name="members"),
    path("members/<int:member_id>/remove/", examiner.remove_member, name="remove_member"),
    path("settings/", examiner.exam_settings, name="settings"),
    path("reset-link/", examiner.reset_link, name="reset_link"),
    path("close/", examiner.close_exam, name="close_exam"),
    path("reopen/", examiner.reopen_exam, name="reopen_exam"),
    path("delete/", examiner.delete_exam, name="delete_exam"),
]

exam_patterns: list[URLPattern | URLResolver] = [
    path("", exams.exam_home, name="exam"),
    path("photos/<int:report_id>/", exams.photo, name="photo"),
    path("leave/", exams.leave_exam, name="leave_exam"),
    path("name/", exams.rename_member, name="rename_member"),
    path("reports/<int:report_id>/delete/", exams.delete_report, name="delete_report"),
    path("invigilator/", include((invigilator_patterns, "invigilator"))),
    path("examiner/", include((examiner_patterns, "examiner"))),
]

urlpatterns = [
    path("", exams.home, name="home"),
    path("exams/new/", exams.create_exam, name="create_exam"),
    path("join/<str:token>/", exams.join, name="join"),
    path("exams/<int:exam_id>/", include(exam_patterns)),
    path("accounts/", include("allauth.urls")),
    # Admin sign-in goes through the Exam Desk page too.
    path("admin/login/", RedirectView.as_view(pattern_name="account_login", query_string=True)),
    path("admin/", admin.site.urls),
]
