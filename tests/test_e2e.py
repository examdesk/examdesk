"""An invigilator on a phone and an examiner on a laptop, each in their own browser."""

import os
import re
import threading
from collections.abc import Iterator

import pytest
import uvicorn
from django.contrib.auth.models import User
from django.core.asgi import get_asgi_application
from playwright.sync_api import Browser, Page, expect
from pytest_django.fixtures import Settings

from examdesk import services
from tests.helpers import named, report

# Playwright's sync API runs an event loop; the ORM calls here are still synchronous.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture
def server(transactional_db: None, settings: Settings) -> Iterator[str]:
    """Exam Desk under uvicorn, as deployed: Django's live server can't stream."""
    settings.WHITENOISE_USE_FINDERS = True  # tests run without `collectstatic`
    config = uvicorn.Config(get_asgi_application(), port=0, timeout_graceful_shutdown=1)
    uvicorn_server = uvicorn.Server(config)
    thread = threading.Thread(target=uvicorn_server.run)
    thread.start()
    while not uvicorn_server.started and thread.is_alive():
        thread.join(0.01)
    port = uvicorn_server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    uvicorn_server.should_exit = True
    thread.join()


def open_link(browser: Browser, url: str, name: str, phone: bool = False) -> Page:
    viewport = {"width": 390, "height": 844} if phone else {"width": 1280, "height": 800}
    page = browser.new_context(viewport=viewport).new_page()  # type: ignore[arg-type]
    page.goto(url)
    page.get_by_label("Name").fill(name)
    page.get_by_role("button", name="Continue").click()
    return page


@pytest.mark.django_db(transaction=True)
def test_report_answer_deliver_and_announce(server: str, browser: Browser, staff: User) -> None:
    exam = services.create_exam(name="Biology Final", owner=staff)
    prof = open_link(browser, f"{server}/join/{exam.examiner_token}/", "Prof")
    invigilator = open_link(browser, f"{server}/join/{exam.invigilator_token}/", "Ann", phone=True)

    # The invigilator's first screen asks for their venue; then they report a new clarification.
    expect(invigilator.get_by_role("heading", name="Which venue are you in?")).to_be_visible()
    invigilator.get_by_label("My venue").fill("hall 1")
    invigilator.get_by_role("button", name="Save", exact=True).click()
    expect(invigilator.get_by_role("button", name="Venue: HALL1")).to_be_visible()
    invigilator.get_by_role("combobox", name="Seat").fill(" 14")
    invigilator.get_by_role("combobox", name="Question").fill("3(b)")
    invigilator.keyboard.press("Tab")
    # No clarification asked about Q3 yet, so New clarification opens.
    invigilator.get_by_label("What is the student asking?").fill("Is x an integer?")
    invigilator.get_by_role("button", name="Save clarification").click()
    expect(invigilator.get_by_role("heading", name="Reported")).to_be_visible()
    expect(invigilator.get_by_text("HALL1 14 · Q3b")).to_be_visible()
    expect(invigilator.get_by_role("link", name="Report another")).to_be_visible()
    invigilator.get_by_role("link", name="Waiting").click()
    expect(invigilator.locator(".place-chip", has_text="14")).to_be_visible()

    # The examiner answers; the invigilator's Deliver badge and Waiting list follow.
    prof.get_by_role("link", name="Is x an integer?").click()
    prof.get_by_role("button", name="State your assumptions.").click()
    prof.get_by_role("button", name="Send", exact=True).click()
    prof.get_by_role("link", name="Seats").click()
    expect(invigilator.locator(".dock .count.action")).to_have_text("1")
    expect(invigilator.get_by_text("Nothing waiting in HALL1")).to_be_visible()
    invigilator.locator(".dock").get_by_role("link", name="Deliver").click()
    invigilator.get_by_role("link", name="HALL1 14: 1 to deliver").click()
    expect(invigilator.get_by_text("State your assumptions.", exact=True)).to_be_visible()
    invigilator.get_by_role("button", name="Delivered to 14").click()
    expect(invigilator.get_by_text("Delivered Q3b to HALL1 14.")).to_be_visible()
    expect(invigilator.get_by_text("All delivered to 14")).to_be_visible()
    invigilator.locator(".dock").get_by_role("link", name="Deliver").click()
    expect(invigilator.get_by_text("Nothing to deliver in HALL1")).to_be_visible()
    expect(invigilator.locator(".dock .count")).to_have_count(0)
    expect(prof.locator("a.seat-pill.state-delivered", has_text="14")).to_be_visible()

    # A delivered seat, under Delivered recently, opens to its delivery and Unmark.
    invigilator.get_by_text("Delivered recently").click()
    invigilator.get_by_role("link", name="HALL1 14: all delivered").click()
    invigilator.locator("details[name=delivered] summary").click()
    expect(invigilator.locator("details[name=delivered] .meta", has_text="Ann")).to_be_visible()
    invigilator.get_by_role("button", name="Unmark").click()
    expect(invigilator.get_by_text("Unmarked HALL1 14.")).to_be_visible()
    invigilator.get_by_role("button", name="Delivered to 14").click()
    expect(prof.locator("a.seat-pill.state-delivered", has_text="14")).to_be_visible()

    # An announcement: the invigilator's alert and badge, and the examiner's venue badges.
    prof.get_by_role("link", name="Announcements").click()
    prof.get_by_label("New announcement").fill("Q2: read f as g.")
    prof.get_by_role("button", name="Announce to all venues", exact=True).click()
    expect(prof.locator(".badge.state-to-read-out", has_text="HALL1")).to_be_visible()
    expect(prof.locator('nav a[href$="/examiner/announcements/"] .count')).to_have_text("1")
    expect(invigilator.locator(".dock .count.urgent")).to_have_text("1")
    alert = invigilator.locator("[data-alert]")
    expect(alert).to_contain_text("Q2: read f as g.")
    alert.get_by_role("button", name="Read out in HALL1").click()
    expect(invigilator.get_by_text("Marked as read out in HALL1.")).to_be_visible()
    expect(alert).to_be_hidden()
    invigilator.locator(".dock").get_by_role("link", name="Read out").click()
    expect(invigilator.get_by_role("button", name="Read out in HALL1: unmark")).to_be_visible()
    expect(invigilator.locator(".dock .count")).to_have_count(0)
    expect(prof.locator(".badge.state-read-out", has_text="HALL1")).to_be_visible()
    expect(prof.locator('nav a[href$="/examiner/announcements/"] .count')).to_have_count(0)

    # Closing the exam reloads every invigilator screen into the closed-exam page.
    prof.get_by_role("link", name="Settings").click()
    prof.get_by_role("button", name="Close exam…").click()
    prof.locator("dialog[open]").get_by_role("button", name="Close exam").click()
    expect(invigilator.get_by_text("This exam is closed.")).to_be_visible()
    expect(invigilator.locator(".dock").get_by_role("link", name="Deliver")).to_be_hidden()


@pytest.mark.django_db(transaction=True)
def test_question_filter_applies_its_picks_when_closed(
    server: str, browser: Browser, staff: User
) -> None:
    exam = services.create_exam(name="Biology Final", owner=staff)
    report(exam, label="Q10", text="Ten?")
    report(exam, seat="21", label="Q2", text="Two?")
    report(exam, seat="34", label="Q3b", text="Three?")
    prof = open_link(browser, f"{server}/join/{exam.examiner_token}/", "Prof")
    prof.get_by_role("link", name="Clarifications").first.click()
    search = prof.get_by_role("combobox", name="Questions")
    search.click()
    prof.get_by_role("option", name="Q3", exact=True).click()
    search.press_sequentially("10")
    search.press("Enter")  # picks Q10, and the menu stays open: nothing applied yet
    expect(prof.get_by_text("Two?")).to_be_visible()
    prof.keyboard.press("Escape")
    expect(prof.get_by_text("Two?")).to_be_hidden()
    expect(prof.get_by_text("Three?")).to_be_visible()  # Q3 covers Q3b
    # Removing a pick with the menu closed applies at once.
    chips = prof.locator(".ts-wrapper.multi .item")
    chips.filter(has_text="Q10").get_by_role("button").click()
    expect(prof.get_by_text("Ten?")).to_be_hidden()
    expect(chips).to_have_count(1)
    # A state tab keeps the other filters.
    prof.get_by_role("link", name="All", exact=True).click()
    expect(prof).to_have_url(re.compile(r"state=all&label=Q3"))
    expect(chips).to_have_count(1)


@pytest.mark.django_db(transaction=True)
def test_find_a_seat_as_you_type(
    server: str, browser: Browser, staff: User, settings: Settings
) -> None:
    settings.EXAMDESK_PAGE_SIZE = 2
    exam = services.create_exam(name="Biology Final", owner=staff)
    for seat in ("1", "2", "14"):
        report(exam, seat=seat)
    prof = open_link(browser, f"{server}/join/{exam.examiner_token}/", "Prof")
    prof.wait_for_url("**/examiner/**")
    prof.goto(f"{server}/exams/{exam.pk}/examiner/seats/?state=waiting")
    # Each pause in typing searches within the state, and the box keeps the focus.
    search = prof.get_by_role("searchbox", name="Find a seat")
    search.press_sequentially("1")
    expect(prof).to_have_url(re.compile(r"\?state=waiting&q=1$"))
    prof.keyboard.type("4")
    expect(prof).to_have_url(re.compile(r"\?state=waiting&q=14$"))
    expect(search).to_have_value("14")
    expect(search).to_be_focused()
    expect(prof.get_by_role("link", name=re.compile("^HALL1 14:"))).to_be_visible()
    expect(prof.get_by_role("link", name=re.compile("^HALL1 2:"))).to_be_hidden()


@pytest.mark.django_db(transaction=True)
def test_a_send_updates_only_what_changed(server: str, browser: Browser, staff: User) -> None:
    exam = services.create_exam(name="Biology Final", owner=staff)
    first = report(exam)
    for n in range(12):
        report(exam, seat=f"{70 + n}", clarification=first.clarification)
    prof = open_link(browser, f"{server}/join/{exam.examiner_token}/", "Prof")
    prof.set_viewport_size({"width": 1000, "height": 500})
    prof.get_by_role("link", name="Is x an integer?").click()
    answer = prof.get_by_role("textbox", name="Answer")
    expect(answer).to_be_visible()
    prof.evaluate("document.querySelector('header').kept = true")  # lost if it's replaced
    answer.fill("Draft not sent")
    prof.get_by_role("button", name="Edit wording").click()
    wording = prof.get_by_role("textbox", name="Wording")
    wording.fill("Is x whole?")
    wording.press("Escape")
    expect(wording).to_be_hidden()
    expect(prof.get_by_role("button", name="Edit wording")).to_contain_text("Is x an integer?")
    prof.get_by_role("button", name="Edit wording").click()
    wording.fill("Is x a whole number?")
    prof.get_by_role("button", name="Save wording").click()
    expect(prof.get_by_role("button", name="Edit wording")).to_contain_text("Is x a whole number?")
    expect(wording).to_be_hidden()
    expect(answer).to_have_value("Draft not sent")
    # Sending from partway down leaves the page where it was.
    answer.fill("Yes.")
    prof.evaluate("document.getElementById('correction').scrollIntoView({block: 'center'})")
    scrolled = prof.evaluate("scrollY")
    assert scrolled > 0
    prof.get_by_role("button", name="Send", exact=True).click()
    expect(prof.get_by_role("status").filter(has_text="Sent.")).to_be_visible()
    expect(prof.get_by_role("paragraph").filter(has_text="Yes.")).to_be_visible()
    bottom = prof.evaluate("document.documentElement.scrollHeight - innerHeight")
    assert abs(prof.evaluate("scrollY") - min(scrolled, bottom)) <= 1  # sub-pixel rounding
    assert prof.evaluate("document.querySelector('header').kept")
    # The sends replaced the page's history entry.
    prof.go_back()
    expect(prof).to_have_url(re.compile(r"/examiner/clarifications/$"))


@pytest.mark.django_db(transaction=True)
def test_changes_by_others_show_live_around_unsent_text(
    server: str, browser: Browser, staff: User
) -> None:
    exam = services.create_exam(name="Biology Final", owner=staff)
    first = report(exam)
    prof = open_link(browser, f"{server}/join/{exam.examiner_token}/", "Prof")
    prof.get_by_role("link", name="Is x an integer?").click()
    expect(prof.get_by_role("textbox", name="Answer")).to_be_visible()
    services.answer_clarification(
        first.clarification, text="Yes.", member=named(exam, "Prof Two"), expected_answer=None
    )
    expect(prof.get_by_role("paragraph").filter(has_text="Yes.")).to_be_visible()
    prof.get_by_role("button", name="Correct answer").click()
    correction = prof.get_by_role("textbox", name="Corrected answer")
    correction.fill("Yes, a whole number.")
    report(exam, seat="21", clarification=first.clarification)
    expect(prof.get_by_role("link", name="HALL1 21")).to_be_visible()
    expect(correction).to_have_value("Yes, a whole number.")
    expect(correction).to_be_visible()


@pytest.mark.django_db(transaction=True)
def test_pages_open_at_the_top_and_keep_the_exam_stream(
    server: str, browser: Browser, staff: User
) -> None:
    exam = services.create_exam(name="Biology Final", owner=staff)
    first = report(exam)
    for n in range(12):
        report(exam, seat=f"{70 + n}", clarification=first.clarification)
    prof = open_link(browser, f"{server}/join/{exam.examiner_token}/", "Prof")
    prof.set_viewport_size({"width": 1000, "height": 500})
    expect(prof).to_have_title("(1) Biology Final · Exam Desk")
    requested: list[str] = []
    prof.on("request", lambda request: requested.append(request.url))
    prof.get_by_role("link", name="Is x an integer?").click()
    answer = prof.get_by_role("textbox", name="Answer")
    expect(answer).to_be_visible()
    prof.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    prof.evaluate("document.querySelector('a[href$=\"/examiner/members/\"]').click()")
    expect(prof.get_by_role("heading", name="People")).to_be_visible()
    assert prof.evaluate("scrollY") == 0
    assert not [url for url in requested if "/events/" in url]  # the exam's pages share one stream
    prof.go_back()
    expect(answer).to_be_visible()
    # A send that can't reach the server keeps its text and says so.
    prof.route(
        "**/*", lambda route: route.abort() if route.request.method == "POST" else route.continue_()
    )
    answer.fill("Yes.")
    prof.get_by_role("button", name="Send", exact=True).click()
    expect(prof.get_by_text("Not sent.")).to_be_visible()
    expect(answer).to_have_value("Yes.")
