"""An invigilator on a phone and an examiner on a laptop, each in their own browser."""

import os
import re

import pytest
from django.contrib.auth.models import User
from playwright.sync_api import Browser, Page, expect
from pytest_django.live_server_helper import LiveServer

from examdesk import services
from tests.helpers import report

# Playwright's sync API runs an event loop; the ORM calls here are still synchronous.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def open_link(browser: Browser, url: str, name: str, phone: bool = False) -> Page:
    viewport = {"width": 390, "height": 844} if phone else {"width": 1280, "height": 800}
    page = browser.new_context(viewport=viewport).new_page()  # type: ignore[arg-type]
    page.goto(url)
    page.get_by_label("Name").fill(name)
    page.get_by_role("button", name="Continue").click()
    return page


@pytest.mark.django_db(transaction=True)
def test_report_answer_deliver_and_announce(
    live_server: LiveServer, browser: Browser, staff: User
) -> None:
    exam = services.create_exam(name="CS2109S Final", owner=staff)
    prof = open_link(browser, f"{live_server.url}/join/{exam.examiner_token}/", "Prof")
    invigilator = open_link(
        browser, f"{live_server.url}/join/{exam.invigilator_token}/", "Ann", phone=True
    )

    # The invigilator's first screen asks for their venue; then they report a new clarification.
    expect(invigilator.get_by_role("heading", name="Which venue are you in?")).to_be_visible()
    invigilator.get_by_label("My venue").fill("lt 19")
    invigilator.get_by_role("button", name="Save").click()
    expect(invigilator.get_by_role("button", name="Venue: LT19")).to_be_visible()
    invigilator.get_by_role("combobox", name="Seat").fill("b 14")
    invigilator.get_by_role("combobox", name="Question").fill("3(b)")
    invigilator.get_by_role("button", name="Next").click()
    expect(invigilator.get_by_role("heading", name="LT19 B14 · Q3b")).to_be_visible()
    invigilator.get_by_label("What is the student asking?").fill("Is x an integer?")
    invigilator.get_by_role("button", name="Save clarification").click()
    expect(invigilator.get_by_role("heading", name="LT19 B14 · Q3b")).to_be_visible()
    expect(invigilator.get_by_role("link", name="Report another question")).to_be_visible()
    invigilator.get_by_role("link", name="Open").click()
    expect(invigilator.locator(".badge.state-open", has_text="B14")).to_be_visible()

    # The examiner answers; the invigilator's Deliver badge and Open list follow by polling.
    prof.get_by_role("link", name="Is x an integer?").click()
    prof.get_by_role("button", name="State your assumptions.").click()
    prof.get_by_role("button", name="Send", exact=True).click()
    prof.get_by_role("link", name="Seats").click()  # the badge turns green by polling
    expect(invigilator.locator(".dock .count.action")).to_have_text("1")
    expect(invigilator.get_by_text("No open clarifications in LT19")).to_be_visible()
    invigilator.get_by_role("link", name="Deliver").click()
    invigilator.get_by_role("link", name="LT19 B14: 1 to deliver").click()
    expect(invigilator.get_by_text("State your assumptions.", exact=True)).to_be_visible()
    invigilator.get_by_role("button", name="Delivered to B14").click()
    expect(invigilator.get_by_text("Delivered Q3b to LT19 B14.")).to_be_visible()
    expect(
        invigilator.locator(".clarification-body .meta", has_text="Delivered by Ann")
    ).to_be_visible()
    invigilator.get_by_role("link", name="Deliver").click()
    expect(invigilator.get_by_text("Nothing to deliver in LT19")).to_be_visible()
    expect(invigilator.locator(".dock .count")).to_have_count(0)
    expect(prof.locator("a.seat-card.state-delivered", has_text="B14")).to_be_visible()

    # A delivered seat, under Recently delivered, opens to its delivery and Unmark.
    invigilator.get_by_text("Recently delivered (1)").click()
    invigilator.get_by_role("link", name="LT19 B14: all delivered").click()
    expect(
        invigilator.locator(".clarification-body .meta", has_text="Delivered by Ann")
    ).to_be_visible()
    invigilator.get_by_role("button", name="Unmark").click()
    expect(invigilator.get_by_text("Unmarked LT19 B14.")).to_be_visible()
    invigilator.get_by_role("button", name="Delivered to B14").click()
    expect(prof.locator("a.seat-card.state-delivered", has_text="B14")).to_be_visible()

    # An announcement: the invigilator's alert and badge, and the examiner's venue badges.
    prof.get_by_role("link", name="Announcements").click()
    prof.get_by_label("New announcement").fill("Q2: read f as g.")
    prof.get_by_role("button", name="Announce", exact=True).click()
    expect(prof.locator(".badge.state-to-read-out", has_text="LT19")).to_be_visible()
    expect(prof.locator('nav a[href$="/examiner/announcements/"] .count')).to_have_text("1")
    expect(invigilator.locator(".dock .count.urgent")).to_have_text("1")
    alert = invigilator.locator("[data-alert]")
    expect(alert).to_contain_text("Q2: read f as g.")
    alert.get_by_role("button", name="Read out in LT19").click()
    expect(invigilator.get_by_text("Marked as read out in LT19.")).to_be_visible()
    expect(alert).to_be_hidden()
    invigilator.get_by_role("link", name="Announce").click()
    expect(invigilator.get_by_role("button", name="Read out in LT19: unmark")).to_be_visible()
    expect(invigilator.locator(".dock .count")).to_have_count(0)
    expect(prof.locator(".badge.state-read-out", has_text="LT19")).to_be_visible()
    expect(prof.locator('nav a[href$="/examiner/announcements/"] .count')).to_have_count(0)

    # Closing the exam reloads every invigilator screen into the closed-exam page.
    prof.get_by_role("link", name="Settings").click()
    prof.get_by_role("button", name="Close exam", exact=True).click()
    prof.get_by_role("button", name="Close the exam").click()
    expect(invigilator.get_by_text("This exam is closed.")).to_be_visible()
    expect(invigilator.get_by_role("link", name="Deliver")).to_be_hidden()


@pytest.mark.django_db(transaction=True)
def test_question_filter_applies_its_picks_when_closed(
    live_server: LiveServer, browser: Browser, staff: User
) -> None:
    exam = services.create_exam(name="CS2109S Final", owner=staff)
    report(exam, label="Q10", text="Ten?")
    report(exam, seat="C1", label="Q2", text="Two?")
    report(exam, seat="D4", label="Q3b", text="Three?")
    prof = open_link(browser, f"{live_server.url}/join/{exam.examiner_token}/", "Prof")
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
    # A single select applies its pick at once, keeping the others.
    prof.get_by_role("combobox", name="State").locator("..").click()  # its box
    prof.get_by_role("option", name="All").click()
    expect(prof).to_have_url(re.compile(r"state=all&label=Q3"))
    expect(chips).to_have_count(1)
