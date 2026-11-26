"""The landing page's demos: the app's own pages for exams that scripts build with the app's
services. `manage.py build_demos` saves them for the landing page when the image is built
(Dockerfile)."""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import User
from django.templatetags.static import static
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from . import services, session
from .labels import normalize_label
from .models import Clarification, Exam, Member, Report, Role
from .seats import seat_url

Device = Literal["window", "phone"]


def render_page(member: Member, url: str) -> str:
    """The page at `url` as `member` sees it: its app bar, content and tab bar."""
    client = Client(headers={"Examdesk-Frame": "1"})
    client_session = client.session
    session.remember(client_session, member)
    client_session.save()
    return client.get(url).content.decode()


@dataclass
class Step:
    name: str
    text: str = ""
    device: str = ""  # who uses it, in a hero demo's steps


@dataclass
class Demo:
    """Pages on a window or a phone, and the actions that step through them."""

    name: str
    description: str  # what the demo shows, for screen readers
    frames: dict[Device, list[str]] = field(default_factory=dict)  # by device, in turn
    steps: list[Step] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    toasts: list[tuple[Device, str]] = field(default_factory=list)

    @property
    def layout(self) -> Literal["hero", "window", "phone"]:
        return "hero" if len(self.frames) == 2 else next(iter(self.frames))

    def add(
        self,
        action: str,
        device: Device | None = None,
        delay: float = 0,
        with_previous: bool = False,
        **options: Any,
    ) -> None:
        """An action at its GSAP position: `delay` seconds after the action before, or
        `with_previous` at its start."""
        position = "<" if with_previous else f"+={delay}"
        self.actions.append({"type": action, "device": device, "position": position, **options})

    def show(self, device: Device, member: Member, url: str, delay: float = 0) -> None:
        """`url` as `member` sees it, after the device's page before."""
        pages = self.frames.setdefault(device, [])
        if pages:
            self.add("next_frame", device, delay)
        pages.append(render_page(member, url))

    def step(self, name: str, text: str = "", device: str = "") -> None:
        self.steps.append(Step(name, text, device))
        self.add("step", delay=0.6, name=name)

    def tap(self, device: Device, on: str, delay: float = 0.3) -> None:
        self.add("tap", device, delay, on=on)

    def type(self, device: Device, on: str, text: str, speed: float = 28) -> None:
        """Type `text` into the field at `on`, `speed` characters a second."""
        self.add("type", device, on=on, text=text, speed=speed)

    def set(self, device: Device, on: str, **props: Any) -> None:
        """Set GSAP `props` (`attr`: attributes) of the elements at `on`."""
        self.add("set", device, on=on, props=props)

    def set_attributes(self, device: Device, on: str, **attributes: str | None) -> None:
        """Set attributes of the elements at `on` before the demo plays; None removes one."""
        self.add("set_attributes", device, on=on, attributes=attributes)

    def scroll(self, device: Device, with_previous: bool = False) -> None:
        """Scroll the device's page to its end."""
        self.add("scroll", device, with_previous=with_previous)

    def toast(self, device: Device, text: str) -> None:
        self.toasts.append((device, text))
        self.add("toast", device, with_previous=True, index=len(self.toasts) - 1)

    def set_active(self, device: Device | Literal["both"], delay: float = 0) -> None:
        """In the hero, highlight the device in use, or both."""
        self.add("set_active", delay=delay, with_previous=not delay, on=device)

    def pause(self, seconds: float) -> None:
        self.add("pause", delay=seconds)


class DemoExam:
    """An exam to script: its members, and reports that asked some minutes ago."""

    def __init__(self) -> None:
        owner = User.objects.create(username=f"demo-{uuid.uuid4()}")
        self.exam: Exam = services.create_exam(
            name="Algorithms Final", owner=owner, time_zone=ZoneInfo(settings.TIME_ZONE)
        )
        services.choose_ai_models(self.exam, vision_model="demo", text_model="demo")

    def member(self, name: str, role: Role, venue: str | None = None) -> Member:
        member = services.add_member(self.exam, name=name, role=role)
        if venue is not None:
            services.set_venue(member, venue)
        return member

    def report(
        self,
        member: Member,
        seat: str,
        label: str = "",
        text: str = "",
        clarification: Clarification | None = None,
        minutes_ago: float = 0,
    ) -> Report:
        report = services.add_report(
            member,
            client_key=uuid.uuid4(),
            venue=member.venue or "",
            seat=seat,
            label=normalize_label(label),
            text=text,
            clarification=clarification,
        )
        if minutes_ago:
            asked = timezone.now() - timedelta(minutes=minutes_ago)
            Report.objects.filter(pk=report.pk).update(created_at=asked)
            if clarification is None:
                Clarification.objects.filter(pk=report.clarification_id).update(created_at=asked)
        return report

    def url(self, name: str, *args: Any, **query: str) -> str:
        url = reverse(name, args=[self.exam.pk, *args])
        return f"{url}?{urlencode(query)}" if query else url


def queue_row(clarification: Clarification) -> str:
    """The queue row of `clarification`."""
    return f'.queue-row[href*="/clarifications/{clarification.pk}/"]'


SEND_BUTTON = "#correction .join > button:first-child"
SAVE_BUTTON = "[data-save]"
NEW_QUESTION = "#new-question"


def report_fields(demo: Demo, seat: str, label: str) -> None:
    """Type the seat and the question number into the Report form."""
    demo.tap("phone", "#id_seat-ts-control", delay=0.6)
    demo.type("phone", "#id_seat-ts-control", seat)
    demo.tap("phone", "#id_label-ts-control")
    demo.type("phone", "#id_label-ts-control", label)


def show_photo(demo: Demo, already_taken: bool = False) -> None:
    """The photo taken for a new question, as Report previews it: appearing as it's taken, or
    there from the start once `already_taken`."""
    demo.set_attributes("phone", "[data-preview] img", src=static("examdesk/handwriting.svg"))
    if already_taken:
        demo.set_attributes("phone", "[data-preview]", hidden=None)
        demo.set_attributes("phone", "[data-add-photo]", hidden="")
    else:
        demo.set("phone", "[data-preview]", hidden=False)
        demo.set("phone", "[data-add-photo]", hidden=True)
        demo.scroll("phone", with_previous=True)


def tap_question_choice(demo: Demo, on: str) -> None:
    """Tap Type question or Take photo and show the question form."""
    demo.tap("phone", on, delay=0.4)
    demo.set("phone", "[data-question-choice]", hidden=True)
    demo.set("phone", "[data-composer]", hidden=False)


def hero() -> Demo:
    question = "Is big-O enough or do you want the exact count?"
    answer = "Big-O is enough. You don't need the exact count."
    demo = Demo(
        "hero",
        "An invigilator reports a question from seat 14 on a phone. It appears in the "
        "examiner's list, the examiner opens it and answers, and the invigilator shows the "
        "answer to seat 14.",
    )
    exam = DemoExam()
    lee = exam.member("Lee", Role.EXAMINER)
    ann = exam.member("Ann", Role.INVIGILATOR, "HALL1")
    ben = exam.member("Ben", Role.INVIGILATOR, "HALL2")
    typo = exam.report(
        ann,
        "32",
        "2a",
        "Is there a typo in 2a? The bound says n² but the code loops n times.",
        minutes_ago=6,
    )
    exam.report(ben, "21", clarification=typo.clarification, minutes_ago=5)
    exam.report(ann, "7", "1", "Can we use the built-in sort?", minutes_ago=2)
    demo.show("window", lee, exam.url("examiner:clarification", typo.clarification_id))
    demo.show("phone", ann, exam.url("invigilator:report"))

    demo.step("Report", device="Invigilator's phone")
    report_fields(demo, "14", "3b")
    demo.show(
        "phone",
        ann,
        exam.url("invigilator:report", venue="HALL1", seat="14", label="3b"),
        delay=0.3,
    )
    demo.tap("phone", f"{NEW_QUESTION} > summary", delay=0.6)
    demo.set("phone", NEW_QUESTION, open=True)
    tap_question_choice(demo, "[data-type-question]")
    demo.tap("phone", "#report-text")
    demo.type("phone", "#report-text", question)
    demo.tap("phone", SAVE_BUTTON, delay=0.4)
    asked = exam.report(ann, "14", "3b", question)
    demo.show("phone", ann, exam.url("invigilator:report_saved", asked.pk))
    demo.show("window", lee, exam.url("examiner:clarification", typo.clarification_id), delay=0.4)
    demo.set_active("both")

    demo.step("Answer", device="Examiner's computer")
    demo.set_active("window", delay=0.8)
    demo.tap("window", queue_row(asked.clarification))
    demo.show("window", lee, exam.url("examiner:clarification", asked.clarification_id))
    demo.tap("window", "#answer-text", delay=0.6)
    demo.type("window", "#answer-text", answer)
    demo.tap("window", SEND_BUTTON)
    services.answer_clarification(
        asked.clarification, text=answer, member=lee, expected_answer=None
    )
    demo.show("window", lee, exam.url("examiner:clarification", asked.clarification_id))
    demo.toast("window", "Sent.")
    demo.show("phone", ann, exam.url("invigilator:report_saved", asked.pk), delay=0.6)
    demo.set_active("both")

    demo.step("Deliver", device="Invigilator's phone")
    demo.set_active("phone", delay=1)
    demo.tap("phone", 'a[href*="/deliver/seat/"]')
    seat_page = seat_url("invigilator:deliver_seat", exam.exam.pk, "HALL1", "14", back="report")
    demo.show("phone", ann, seat_page)
    demo.tap("phone", 'form[action$="/delivered/"] button', delay=1.6)
    asked.refresh_from_db()
    services.mark_delivered(asked, answer=asked.clarification.current_answer, member=ann)
    demo.show("phone", ann, seat_page)
    demo.toast("phone", "Delivered Q3b to HALL1 14.")
    demo.show("window", lee, exam.url("examiner:clarification", asked.clarification_id))
    demo.set_active("both")
    demo.pause(1.6)
    return demo


def report() -> Demo:
    demo = Demo(
        "report",
        "An invigilator reports seat 27 and adds it to a clarification already asked about 3b, "
        "then reports seat 32 with a photo of a new question.",
    )
    exam = DemoExam()
    lee = exam.member("Lee", Role.EXAMINER)
    ann = exam.member("Ann", Role.INVIGILATOR, "HALL1")
    duplicates = exam.report(ann, "5", "3a", "Can the array have duplicates?", minutes_ago=9)
    services.answer_clarification(
        duplicates.clarification, text="Yes.", member=lee, expected_answer=None
    )
    big_o = exam.report(
        ann, "14", "3b", "Is big-O enough or do you want the exact count?", minutes_ago=3
    )
    demo.show("phone", ann, exam.url("invigilator:report"))

    demo.step("Seat and question", "Type the seat and the question number.")
    report_fields(demo, "27", "3b")

    demo.step(
        "Already asked",
        "Clarifications about the same question show first. Tap one and the seat joins it.",
    )
    demo.show(
        "phone",
        ann,
        exam.url("invigilator:report", venue="HALL1", seat="27", label="3b"),
        delay=0.5,
    )
    demo.tap("phone", f'button.match-button[value="{big_o.clarification_id}"]', delay=1.6)
    joined = exam.report(ann, "27", clarification=big_o.clarification)
    demo.show("phone", ann, exam.url("invigilator:report_saved", joined.pk))

    demo.step("New question", "Otherwise type it, or take a photo and AI fills in the text.")
    question = "In 3c, can we assume the graph is connected?"
    demo.tap("phone", '.action-bar a[href$="/report/"]', delay=1.4)
    demo.show("phone", ann, exam.url("invigilator:report"))
    report_fields(demo, "32", "3c")
    demo.show(
        "phone",
        ann,
        exam.url("invigilator:report", venue="HALL1", seat="32", label="3c"),
        delay=0.3,
    )
    demo.tap("phone", f"{NEW_QUESTION} > summary", delay=0.6)
    demo.set("phone", NEW_QUESTION, open=True)
    demo.scroll("phone")
    tap_question_choice(demo, "[data-question-choice] label")
    demo.set("phone", "[data-text-skeleton], [data-transcribe-status]", hidden=False)
    demo.set("phone", "[data-photo-actions], [data-composer] .action-bar", hidden=True)
    demo.set("phone", "#report-text", attr={"placeholder": ""})
    show_photo(demo)
    demo.pause(1.2)
    demo.show(
        "phone",
        ann,
        exam.url("invigilator:report", venue="HALL1", seat="32", label="3c", text=question),
    )
    show_photo(demo, already_taken=True)
    demo.tap("phone", SAVE_BUTTON, delay=0.6)
    new = exam.report(ann, "32", "3c", question)
    demo.show("phone", ann, exam.url("invigilator:report_saved", new.pk))
    demo.pause(1.8)
    return demo


def answer() -> Demo:
    answer_3b = "Big-O is enough. You don't need the exact number of comparisons."
    answer_2a = "Correction to question 2a: the bound should read n, not n²."
    demo = Demo(
        "answer",
        "The examiner's list of clarifications. Two duplicates of 3b merge into it, an AI draft "
        "answers it, and the answer to 2a is announced to every venue.",
    )
    exam = DemoExam()
    lee = exam.member("Lee", Role.EXAMINER)
    ann = exam.member("Ann", Role.INVIGILATOR, "HALL1")
    ben = exam.member("Ben", Role.INVIGILATOR, "HALL2")
    typo = exam.report(
        ann,
        "32",
        "2a",
        "Is there a typo in 2a? The bound says n² but the code loops n times.",
        minutes_ago=6,
    )
    exam.report(ben, "21", clarification=typo.clarification, minutes_ago=5)
    big_o = exam.report(
        ann, "14", "3b", "For 3b, is big-O enough or do you want the exact number?", minutes_ago=3
    )
    first = exam.report(ann, "27", "3b", "Exact count of comparisons in 3b?", minutes_ago=2)
    sort = exam.report(ann, "7", "1", "Can we use the built-in sort?", minutes_ago=8)
    services.answer_clarification(
        sort.clarification, text="Yes, any built-in sort is fine.", member=lee, expected_answer=None
    )
    page = exam.url("examiner:clarification", big_o.clarification_id)
    demo.show("window", lee, page)

    demo.step("Longest wait first")
    second = exam.report(ben, "12", "3b", "In 3b, do we count comparisons or swaps?")
    demo.show("window", lee, page, delay=0.8)
    demo.pause(1.4)

    demo.step("Merge duplicates")
    for duplicate in (first, second):
        checkbox = f'input[form="merge-selected"][value="{duplicate.clarification_id}"]'
        demo.tap("window", checkbox)
        demo.set("window", checkbox, checked=True)
    demo.tap("window", "#merge-selected > button", delay=0.3)
    services.merge_clarifications(
        sources=[first.clarification, second.clarification], target=big_o.clarification
    )
    demo.show("window", lee, page)
    demo.toast("window", "Merged into Q3b.")

    demo.step("Draft with AI")
    demo.tap("window", "[data-draft]", delay=1.2)
    demo.type("window", "#answer-text", answer_3b, speed=90)
    demo.tap("window", SEND_BUTTON, delay=0.6)
    services.answer_clarification(
        big_o.clarification, text=answer_3b, member=lee, expected_answer=None
    )
    demo.show("window", lee, page)
    demo.toast("window", "Sent.")

    demo.step("Announce")
    demo.tap("window", queue_row(typo.clarification), delay=1.2)
    demo.show("window", lee, exam.url("examiner:clarification", typo.clarification_id))
    demo.tap("window", "#answer-text", delay=0.6)
    demo.type("window", "#answer-text", answer_2a)
    demo.tap("window", '[popovertarget="send-menu"]', delay=0.3)
    demo.set("window", "#send-menu", attr={"data-open": 1})
    demo.tap("window", "#send-menu button", delay=0.4)
    demo.set("window", "#send-menu", attr={"data-open": 0})
    demo.set("window", "#announce-dialog blockquote", textContent=answer_2a)
    demo.set("window", "#announce-dialog", attr={"data-open": 1})
    demo.tap("window", "#announce-dialog .btn-primary", delay=0.9)
    services.announce(lee, text=answer_2a, clarification=typo.clarification)
    demo.show("window", lee, exam.url("examiner:clarification", typo.clarification_id))
    demo.toast("window", "Announced Q2a to all venues.")
    demo.pause(2)
    return demo


def deliver() -> Demo:
    answer = "Big-O is enough. You don't need the exact count."
    demo = Demo(
        "deliver",
        "An invigilator's list of seats with answers. Seat 8 is with Ben, so the invigilator "
        "takes seat 14, shows the answer and marks it delivered. When the examiner corrects the "
        "answer, seat 14 comes back to the list.",
    )
    exam = DemoExam()
    lee = exam.member("Lee", Role.EXAMINER)
    ann = exam.member("Ann", Role.INVIGILATOR, "HALL1")
    ben = exam.member("Ben", Role.INVIGILATOR, "HALL1")
    big_o = exam.report(
        ben, "8", "3b", "Is big-O enough or do you want the exact count?", minutes_ago=6
    )
    fourteen = exam.report(ann, "14", clarification=big_o.clarification, minutes_ago=5)
    sort = exam.report(ann, "27", "1", "Can we use the built-in sort?", minutes_ago=4)
    first = services.answer_clarification(
        big_o.clarification, text=answer, member=lee, expected_answer=None
    )
    services.answer_clarification(
        sort.clarification, text="Yes, any built-in sort is fine.", member=lee, expected_answer=None
    )
    deliver_page = exam.url("invigilator:to_deliver")
    demo.show("phone", ann, deliver_page)

    demo.step("Seats with an answer", "Every seat in your hall with an answer to show.")
    demo.pause(1)

    demo.step(
        "One invigilator per seat", "Opening a seat shows the other invigilators that you have it."
    )
    services.claim_seat(ben, venue="HALL1", seat="8")
    demo.show("phone", ann, deliver_page, delay=0.4)
    demo.tap("phone", '.deliver-row[href*="seat=14"]', delay=1.4)
    seat_page = seat_url("invigilator:deliver_seat", exam.exam.pk, "HALL1", "14")
    demo.show("phone", ann, seat_page)

    demo.step("Delivered", "Show the student the answer, then mark it delivered.")
    demo.tap("phone", 'form[action$="/delivered/"] button', delay=1.6)
    services.mark_delivered(fourteen, answer=first, member=ann)
    demo.show("phone", ann, seat_page)
    demo.toast("phone", "Delivered Q3b to HALL1 14.")

    demo.step(
        "Corrected",
        "If the examiner corrects an answer, every seat that had the old one comes back.",
    )
    demo.tap("phone", '.dock a[href$="/deliver/"]', delay=1.4)
    demo.show("phone", ann, deliver_page)
    services.answer_clarification(
        big_o.clarification,
        text="Big-O is enough. Give it in terms of n.",
        member=lee,
        expected_answer=first.pk,
    )
    demo.show("phone", ann, deliver_page, delay=0.9)
    demo.pause(2)
    return demo


def build() -> dict[str, dict[str, Any]]:
    """The demos as the landing page reads them."""
    demos = [hero(), report(), answer(), deliver()]
    return {demo.name: asdict(demo) | {"layout": demo.layout} for demo in demos}


def save(path: Path) -> None:
    path.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n")
