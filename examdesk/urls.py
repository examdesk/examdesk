from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path
from django.views.generic import RedirectView

from .views import examiner, exams, invigilator, live

invigilator_patterns = [
    path("venue/", invigilator.choose_venue, name="choose_venue"),
    path("report/", invigilator.report, name="report"),
    path("report/transcribe/", invigilator.transcribe_photo, name="transcribe_photo"),
    path("reports/<int:report_id>/", invigilator.report_saved, name="report_saved"),
    path("open/", invigilator.open_clarifications, name="open_clarifications"),
    path("deliver/", invigilator.to_deliver, name="to_deliver"),
    path("deliver/seat/", invigilator.deliver_seat, name="deliver_seat"),
    path("reports/<int:report_id>/delivered/", invigilator.mark_delivered, name="mark_delivered"),
    path(
        "deliveries/<int:delivery_id>/undo/",
        invigilator.undo_delivery,
        name="undo_delivery",
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
        "announcements/<int:announcement_id>/undo-read-out/",
        invigilator.undo_read_out,
        name="undo_read_out",
    ),
]

clarification_patterns = [
    path("", examiner.clarification, name="clarification"),
    path("answer/", examiner.answer_clarification, name="answer_clarification"),
    path("duplicates/", examiner.clarification_duplicates, name="clarification_duplicates"),
    path("draft/", examiner.draft_answer, name="draft_answer"),
    path("withdraw/", examiner.withdraw_answer, name="withdraw_answer"),
    path("announce/", examiner.announce_clarification, name="announce_clarification"),
    path("reopen/", examiner.reopen_clarification, name="reopen_clarification"),
    path("reword/", examiner.reword_clarification, name="reword_clarification"),
    path("relabel/", examiner.relabel_clarification, name="relabel_clarification"),
]

examiner_patterns: list[URLPattern | URLResolver] = [
    path("clarifications/", examiner.clarifications, name="clarifications"),
    path("clarifications/merge/", examiner.merge_clarifications, name="merge_clarifications"),
    path("clarifications/<int:clarification_id>/", include(clarification_patterns)),
    path("reports/<int:report_id>/move/", examiner.move_report, name="move_report"),
    path("announcements/", examiner.announcements, name="announcements"),
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
    path("edit/", examiner.edit_exam, name="edit_exam"),
    path("ai-service/connect/", examiner.connect_ai_service, name="connect_ai_service"),
    path("ai-service/disconnect/", examiner.disconnect_ai_service, name="disconnect_ai_service"),
    path("ai-service/models/", examiner.choose_ai_models, name="choose_ai_models"),
    path("files/", examiner.add_file, name="add_file"),
    path("files/<int:file_id>/", examiner.exam_file, name="file"),
    path("files/<int:file_id>/delete/", examiner.delete_file, name="delete_file"),
    path(
        "files/<int:file_id>/pages/<int:number>/image/",
        examiner.page_image,
        name="page_image",
    ),
    path("files/<int:file_id>/pages/<int:number>/read/", examiner.read_page, name="read_page"),
    path("reset-link/", examiner.reset_link, name="reset_link"),
    path("close/", examiner.close_exam, name="close_exam"),
    path("reopen/", examiner.reopen_exam, name="reopen_exam"),
    path("delete/", examiner.delete_exam, name="delete_exam"),
]

exam_patterns: list[URLPattern | URLResolver] = [
    path("", exams.exam_home, name="exam"),
    path("photos/<int:report_id>/", exams.photo, name="photo"),
    path("leave/", exams.leave_exam, name="leave_exam"),
    path("events/", live.events, name="events"),
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
