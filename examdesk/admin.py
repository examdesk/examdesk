from django.contrib import admin

from .models import (
    Announcement,
    Answer,
    Clarification,
    Delivery,
    Exam,
    Member,
    Readout,
    Report,
    SeatClaim,
)


class MemberInline(admin.TabularInline):
    model = Member
    fields = ["name", "role", "venue", "last_seen", "removed"]
    readonly_fields = ["last_seen"]
    extra = 0


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "is_live", "created_at"]
    list_filter = ["is_live"]
    search_fields = ["name", "owner__email"]
    autocomplete_fields = ["owner"]
    readonly_fields = ["version", "created_at"]
    inlines = [MemberInline]


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ["name", "exam", "role", "venue", "last_seen", "removed"]
    list_filter = ["role", "removed"]
    list_select_related = ["exam"]
    search_fields = ["name", "exam__name"]
    autocomplete_fields = ["exam"]


class AnswerInline(admin.TabularInline):
    model = Answer
    fields = ["text", "member", "created_at"]
    readonly_fields = ["created_at"]
    autocomplete_fields = ["member"]
    extra = 0


class ReportInline(admin.TabularInline):
    model = Report
    fields = ["venue", "seat", "text", "photo", "member", "created_at"]
    readonly_fields = ["created_at"]
    autocomplete_fields = ["member"]
    extra = 0


@admin.register(Clarification)
class ClarificationAdmin(admin.ModelAdmin):
    list_display = ["label", "summary", "exam", "state", "created_at"]
    list_filter = ["closed_reason"]
    list_select_related = ["exam"]
    search_fields = ["label", "wording", "exam__name"]
    autocomplete_fields = ["exam", "current_answer", "merged_into"]
    readonly_fields = ["created_at"]
    inlines = [AnswerInline, ReportInline]


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ["__str__", "clarification", "member", "created_at"]
    list_select_related = ["clarification", "member"]
    search_fields = ["text", "clarification__label"]
    autocomplete_fields = ["clarification", "member"]
    readonly_fields = ["created_at"]


class DeliveryInline(admin.TabularInline):
    model = Delivery
    fields = ["answer", "member", "created_at"]
    readonly_fields = ["created_at"]
    autocomplete_fields = ["answer", "member"]
    extra = 0


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ["__str__", "clarification", "member", "created_at"]
    list_filter = ["venue"]
    list_select_related = ["clarification", "member"]
    search_fields = ["venue", "seat", "text", "clarification__label"]
    autocomplete_fields = ["clarification", "member"]
    readonly_fields = ["client_key", "created_at"]
    inlines = [DeliveryInline]


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ["__str__", "answer", "created_at"]
    list_select_related = ["report", "answer", "member"]
    search_fields = ["report__venue", "report__seat", "member__name"]
    autocomplete_fields = ["report", "answer", "member"]
    readonly_fields = ["created_at"]


@admin.register(SeatClaim)
class SeatClaimAdmin(admin.ModelAdmin):
    list_display = ["__str__", "exam", "claimed_at"]
    list_select_related = ["exam", "member"]
    search_fields = ["venue", "seat", "member__name"]
    autocomplete_fields = ["exam", "member"]


class ReadoutInline(admin.TabularInline):
    model = Readout
    fields = ["venue", "member", "created_at"]
    readonly_fields = ["created_at"]
    autocomplete_fields = ["member"]
    extra = 0


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ["__str__", "label", "exam", "member", "created_at"]
    list_select_related = ["exam", "member"]
    search_fields = ["text", "label", "exam__name"]
    autocomplete_fields = ["exam", "clarification", "member"]
    readonly_fields = ["created_at"]
    inlines = [ReadoutInline]


@admin.register(Readout)
class ReadoutAdmin(admin.ModelAdmin):
    list_display = ["__str__", "announcement", "created_at"]
    list_filter = ["venue"]
    list_select_related = ["announcement", "member"]
    search_fields = ["venue", "member__name"]
    autocomplete_fields = ["announcement", "member"]
    readonly_fields = ["created_at"]
