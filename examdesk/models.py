import secrets
from collections.abc import Iterable
from datetime import datetime
from typing import Self

from django.conf import settings
from django.db import models
from django.db.models import Exists, OuterRef, Prefetch, Subquery
from django.db.models.functions import Lower
from django.utils import timezone


def new_token() -> str:
    return secrets.token_urlsafe(24)


class Role(models.TextChoices):
    INVIGILATOR = "invigilator", "Invigilator"
    EXAMINER = "examiner", "Examiner"


class Exam(models.Model):
    name = models.CharField(max_length=200)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    examiner_token = models.CharField(max_length=64, unique=True, default=new_token)
    invigilator_token = models.CharField(max_length=64, unique=True, default=new_token)
    is_live = models.BooleanField(default=True)
    version = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name

    @staticmethod
    def token_field(role: Role) -> str:
        return "examiner_token" if role == Role.EXAMINER else "invigilator_token"

    def token(self, role: Role) -> str:
        token: str = getattr(self, self.token_field(role))
        return token


class ClarificationQuerySet(models.QuerySet["Clarification"]):
    def in_state(self, state: Clarification.State) -> Self:
        if state in Clarification.ClosedReason:
            return self.filter(closed_reason=state)
        return self.filter(
            closed_reason="", current_answer__isnull=state == Clarification.State.OPEN
        )

    def with_photo(self) -> Self:
        """Annotate `photo_report_id`, the pk of a report with a photo."""
        photos = Report.objects.filter(clarification=OuterRef("pk")).exclude(photo="")
        return self.annotate(photo_report_id=photos.values("pk")[:1])


class Clarification(models.Model):
    class State(models.TextChoices):
        OPEN = "open", "Open"
        ANSWERED = "answered", "Answered"
        ANNOUNCED = "announced", "Announced"
        MERGED = "merged", "Merged"

    class ClosedReason(models.TextChoices):
        ANNOUNCED = "announced"
        MERGED = "merged"

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="clarifications")
    label = models.CharField(max_length=30)
    wording = models.TextField(blank=True)
    current_answer = models.ForeignKey(
        "Answer", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    closed_reason = models.CharField(max_length=10, choices=ClosedReason, blank=True)
    merged_into = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ClarificationQuerySet.as_manager()
    # from with_photo()
    photo_report_id: int | None

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.label}: {self.wording[:40]}"

    @property
    def state(self) -> State:
        if self.closed_reason:
            return self.State(self.closed_reason)
        return self.State.ANSWERED if self.current_answer_id else self.State.OPEN

    @property
    def summary(self) -> str:
        """The wording, or a stand-in for a clarification asked only by photo."""
        return self.wording or "(photo)"

    @property
    def texts(self) -> list[str]:
        """The wording and what each seat asked."""
        return [self.wording, *(report.text for report in self.reports.all())]


class Answer(models.Model):
    clarification = models.ForeignKey(
        Clarification, on_delete=models.CASCADE, related_name="answers"
    )
    text = models.TextField()
    member = models.ForeignKey("Member", on_delete=models.RESTRICT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return self.text[:40]


def photo_path(report: Report, filename: str) -> str:
    return f"exams/{report.clarification.exam_id}/{filename}"


class ReportQuerySet(models.QuerySet["Report"]):
    def of_exam(self, exam: Exam) -> Self:
        return self.filter(clarification__exam=exam)

    def at_seat(self, exam: Exam, venue: str, seat: str) -> Self:
        return self.of_exam(exam).filter(venue=venue, seat=seat)

    def in_venue_of(self, member: Member) -> Self:
        """The member's exam, in their venue or in all venues if none is set."""
        reports = self.of_exam(member.exam)
        return reports.filter(venue=member.venue) if member.venue else reports

    def with_state(self) -> Self:
        """Annotate `delivered`, `delivered_at` (of the current answer) and `read_out`."""
        current = Delivery.objects.filter(
            report=OuterRef("pk"), answer=OuterRef("clarification__current_answer")
        )
        read_out = Readout.objects.filter(
            announcement__clarification=OuterRef("clarification"), venue=OuterRef("venue")
        )
        return self.select_related("clarification__current_answer").annotate(
            delivered=Exists(current),
            delivered_at=Subquery(current.values("created_at")[:1]),
            read_out=Exists(read_out),
        )

    def with_deliveries(self) -> Self:
        """Prefetch deliveries for `current_delivery` and `superseded_deliveries`."""
        deliveries = Delivery.objects.select_related("answer", "member")
        return self.prefetch_related(Prefetch("deliveries", deliveries, to_attr="all_deliveries"))

    def open(self) -> Self:
        return self.filter(
            clarification__in=Clarification.objects.in_state(Clarification.State.OPEN)
        )

    def answered(self) -> Self:
        return self.filter(
            clarification__in=Clarification.objects.in_state(Clarification.State.ANSWERED)
        )

    def to_deliver(self) -> Self:
        return self.answered().with_state().filter(delivered=False)


class Report(models.Model):
    """One seat asking one clarification."""

    class State(models.TextChoices):
        OPEN = "open", "Open"
        TO_DELIVER = "to deliver", "To deliver"
        DELIVERED = "delivered", "Delivered"
        TO_READ_OUT = "to read out", "To read out"
        READ_OUT = "read out", "Read out"

    PROGRESS = [State.OPEN, State.TO_READ_OUT, State.TO_DELIVER, State.DELIVERED]
    UNRESOLVED = PROGRESS[:3]

    clarification = models.ForeignKey(
        Clarification, on_delete=models.CASCADE, related_name="reports"
    )
    venue = models.CharField(max_length=30)
    seat = models.CharField(max_length=30)
    text = models.TextField(blank=True)
    photo = models.ImageField(upload_to=photo_path, blank=True)
    member = models.ForeignKey("Member", on_delete=models.RESTRICT, related_name="+")
    client_key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ReportQuerySet.as_manager()
    # from with_state()
    delivered: bool
    delivered_at: datetime | None
    read_out: bool
    # from with_deliveries()
    all_deliveries: list[Delivery]

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.venue} {self.seat}"

    @property
    def state(self) -> State:
        """Requires `with_state()`."""
        clarification = self.clarification
        if clarification.closed_reason:  # announced; merged ones have no reports
            return self.State.READ_OUT if self.read_out else self.State.TO_READ_OUT
        if clarification.current_answer_id is None:
            return self.State.OPEN
        return self.State.DELIVERED if self.delivered else self.State.TO_DELIVER

    @property
    def current_delivery(self) -> Delivery | None:
        """The delivery of the current answer. Requires `with_deliveries()`."""
        current = self.clarification.current_answer_id
        return next((d for d in self.all_deliveries if d.answer_id == current), None)

    @property
    def superseded_deliveries(self) -> list[Delivery]:
        """Deliveries of answers since corrected. Requires `with_deliveries()`."""
        current = self.clarification.current_answer_id
        return [d for d in self.all_deliveries if d.answer_id != current]


class Delivery(models.Model):
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="deliveries")
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name="deliveries")
    member = models.ForeignKey("Member", on_delete=models.RESTRICT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["report", "answer"], name="one_delivery_per_answer")
        ]
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "deliveries"

    def __str__(self) -> str:
        return f"{self.report} by {self.member.name}"


class SeatClaim(models.Model):
    """Someone delivering to a seat, so others skip it. Released when they navigate away."""

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="claims")
    venue = models.CharField(max_length=30)
    seat = models.CharField(max_length=30)
    member = models.ForeignKey("Member", on_delete=models.CASCADE, related_name="+")
    claimed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exam", "venue", "seat"], name="one_claim_per_seat")
        ]

    def __str__(self) -> str:
        return f"{self.venue} {self.seat} by {self.member.name}"

    def holds(self, undelivered: Iterable[Report]) -> bool:
        """Not expired, and no answer is newer than the claim."""
        answers = (report.clarification.current_answer for report in undelivered)
        recent = timezone.now() - self.claimed_at < settings.EXAMDESK_SEAT_CLAIM_FOR
        return recent and all(
            answer is None or answer.created_at <= self.claimed_at for answer in answers
        )


class Announcement(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="announcements")
    label = models.CharField(max_length=30, blank=True)
    clarification = models.ForeignKey(
        Clarification, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    text = models.TextField()
    member = models.ForeignKey("Member", on_delete=models.RESTRICT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return self.text[:40]


class Readout(models.Model):
    """An announcement read out in a venue."""

    announcement = models.ForeignKey(
        Announcement, on_delete=models.CASCADE, related_name="readouts"
    )
    venue = models.CharField(max_length=30)
    member = models.ForeignKey("Member", on_delete=models.RESTRICT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["announcement", "venue"], name="one_readout_per_venue")
        ]
        ordering = ["venue"]

    def __str__(self) -> str:
        return f"{self.venue} by {self.member.name}"


class MemberQuerySet(models.QuerySet["Member"]):
    def active(self) -> Self:
        """Not removed."""
        return self.filter(removed=False)


class Member(models.Model):
    """Someone on the exam, on any number of devices.

    Removing a member keeps the row for the history they made; rejoining restores it.
    """

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="members")
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=20, choices=Role)
    # None until asked, "" for all venues
    venue = models.CharField(max_length=30, null=True, blank=True)  # noqa: DJ001
    last_seen = models.DateTimeField(default=timezone.now)
    removed = models.BooleanField(default=False)

    objects = MemberQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint("exam", Lower("name"), name="one_member_per_name")]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_role_display()})"

    @property
    def is_examiner(self) -> bool:
        return self.role == Role.EXAMINER

    @property
    def online(self) -> bool:
        return timezone.now() - self.last_seen < settings.EXAMDESK_ONLINE_FOR
