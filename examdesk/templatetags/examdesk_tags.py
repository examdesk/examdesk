from collections.abc import Iterable
from datetime import datetime

from django import template
from django.conf import settings
from django.contrib.messages.storage.base import Message
from django.forms import BoundField, Form
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.text import Truncator
from lucide.templatetags.lucide import lucide

from .. import forms, seats
from ..models import Clarification

register = template.Library()

STATE_ICONS = {
    # Clarification.State and Report.State
    "open": "clock",
    "answered": "check",
    "announced": "megaphone",
    "merged": "git-merge",
    # Report.State
    "to deliver": "send",
    "delivered": "check",
    "to read out": "megaphone",
    "read out": "check",
}


@register.filter
def wait(value: datetime | None) -> str:
    """ "<1 min", "4 min", "1 h 05 min"."""
    if value is None:
        return ""
    minutes = int((timezone.now() - value).total_seconds() // 60)
    if minutes < 1:
        return "<1 min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02} min" if hours else f"{minutes} min"


@register.filter
def wait_level(value: datetime | None) -> str:
    """CSS class for how long something has waited."""
    if value is None:
        return ""
    waited = timezone.now() - value
    if waited >= settings.EXAMDESK_VERY_LATE_AFTER:
        return "very-late"
    return "late" if waited >= settings.EXAMDESK_LATE_AFTER else ""


@register.filter
def initials(name: str) -> str:
    """ "Prof Lee" -> "PL"."""
    return "".join(word[0] for word in name.split()[:2]).upper()


@register.filter
def unique(messages: Iterable[Message]) -> list[Message]:
    """Drop duplicate flash messages, e.g. from a retried POST."""
    return list({(m.level, str(m)): m for m in messages}.values())


@register.filter
def state_icon(state: str) -> str:
    return STATE_ICONS[state]


@register.simple_tag
def icon(name: str, title: str = "", css: str = "") -> SafeString:
    """A Lucide icon; without a `title` it is decorative."""
    attrs = {"class": f"icon {css}".strip()} | (
        {"role": "img"} if title else {"aria-hidden": "true"}
    )
    svg = lucide(name, size=24, **attrs)
    if title:
        svg = svg.replace(">", format_html("><title>{}</title>", title), 1)
    return mark_safe(svg)


@register.simple_tag
def seat_url(view: str, exam_id: int, venue: str, seat: str, **params: str | int) -> str:
    """See `seats.seat_url`."""
    return seats.seat_url(view, exam_id, venue, seat, **params)


@register.filter
def form_errors(form: Form) -> str:
    return forms.form_errors(form)


@register.filter
def titled(clarification: Clarification, length: int = 60) -> str:
    """ "Q3b · Is x an integer?", the summary cut to `length` characters."""
    return f"{clarification.label} · {Truncator(clarification.summary).chars(length)}"


@register.filter
def with_placeholder(field: BoundField, placeholder: str) -> SafeString:
    """For forms from libraries, such as allauth, whose placeholders we don't set."""
    return field.as_widget(attrs={"placeholder": placeholder})
