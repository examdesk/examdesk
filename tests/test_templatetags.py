from datetime import timedelta

import pytest
from django.utils import timezone

from examdesk.models import Clarification, Report
from examdesk.templatetags.examdesk_tags import (
    icon,
    markdown,
    seat_url,
    state_icon,
    wait,
    wait_level,
)


def test_wait_in_minutes_and_its_level() -> None:
    now = timezone.now()
    assert wait(now - timedelta(seconds=59)) == "<1 min"
    assert wait(now - timedelta(minutes=4, seconds=59)) == "4 min"
    assert wait(now - timedelta(minutes=65)) == "1 h 05 min"
    assert wait(None) == ""
    assert wait_level(now - timedelta(minutes=4, seconds=59)) == ""
    assert wait_level(now - timedelta(minutes=5)) == "late"
    assert wait_level(now - timedelta(minutes=10)) == "very-late"


@pytest.mark.parametrize("state", [*Clarification.State, *Report.State])
def test_every_state_has_an_icon(state: str) -> None:
    assert state_icon(state)


def test_seat_url_leaves_out_empty_params() -> None:
    assert seat_url("invigilator:deliver_seat", 1, "HALL 1", "14", report=7, back="") == (
        "/exams/1/invigilator/deliver/seat/?venue=HALL+1&seat=14&report=7"
    )


def test_icon_is_decorative_unless_titled() -> None:
    assert 'class="icon size-4" aria-hidden="true">' in icon("clock", css="size-4")
    assert 'role="img"><title>Asked &lt;at&gt;</title>' in icon("clock", "Asked <at>")


def test_markdown_renders_maths_as_mathml_and_escapes_html() -> None:
    html = markdown("## 1B\n\nThe state is $(r, k)$.\n\n$$\nx_1\n$$\n\n<script>x</script>")
    assert "<h2>1B</h2>" in html
    assert '<math xmlns="http://www.w3.org/1998/Math/MathML" display="inline">' in html
    assert 'display="block"' in html
    assert "&lt;script&gt;" in html


def test_markdown_shows_unparsable_latex_as_code() -> None:
    assert "<code>x_1_2</code>" in markdown("$x_1_2$")
