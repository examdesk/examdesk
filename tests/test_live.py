"""Pages subscribe to their exam's versions, and their members count as seen while subscribed."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import cast

import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.http import StreamingHttpResponse
from django.test import AsyncClient, Client
from django.utils import timezone
from pytest_django.fixtures import Settings

from examdesk import services
from examdesk.models import Exam, Member
from tests.helpers import member, report, url

pytestmark = pytest.mark.django_db(transaction=True)


def changed_event(version: int) -> bytes:
    return f"id: {version}\nevent: changed\ndata:\n\n".encode()


async def events(device: Client, path: str, **headers: str) -> AsyncGenerator[bytes]:
    client = AsyncClient()
    client.cookies = device.cookies
    response = await client.get(path, headers=headers)
    assert (response.status_code, response["Content-Type"]) == (200, "text/event-stream")
    async for chunk in cast(StreamingHttpResponse, response):
        yield chunk


def read_first[T](
    stream: AsyncGenerator[bytes], after: Callable[[], Awaitable[T]] | None = None
) -> tuple[bytes, T | None]:
    """The stream's first chunk, and what `after` returns after it."""

    async def run() -> tuple[bytes, T | None]:
        first = await anext(stream)
        try:
            return first, after and await after()
        finally:
            await stream.aclose()
            await sync_to_async(connections.close_all)()

    with ThreadPoolExecutor(1) as executor:  # Playwright's tests leave a loop running in this one
        return executor.submit(asyncio.run, run()).result()


def version(exam: Exam) -> int:
    return Exam.objects.get(pk=exam.pk).version


def test_a_stream_sends_versions_newer_than_its_page(examiner: Client, exam: Exam) -> None:
    page_version = version(exam)
    report(exam)  # a change after the page rendered
    stream = events(examiner, f"{url('events', exam)}?version={page_version}")

    async def next_version() -> bytes:
        await sync_to_async(report)(exam, seat="21")
        return await anext(stream)

    current = version(exam)
    assert read_first(stream, next_version) == (changed_event(current), changed_event(current + 1))


def test_a_reconnect_skips_versions_up_to_its_last_event_id(
    examiner: Client, exam: Exam, settings: Settings
) -> None:
    settings.EXAMDESK_HEARTBEAT_EVERY = timedelta(milliseconds=10)
    report(exam)
    stream = events(
        examiner, f"{url('events', exam)}?version=0", **{"Last-Event-ID": str(version(exam))}
    )

    assert read_first(stream) == (b"data:\n\n", None)


def test_a_device_turned_away_gets_one_version_to_refresh_its_page(exam: Exam) -> None:
    stream = events(Client(), f"{url('events', exam)}?version=3")

    async def remaining_chunks() -> list[bytes]:
        return [chunk async for chunk in stream]

    assert read_first(stream, remaining_chunks) == (changed_event(4), [])


def test_deleting_the_exam_refreshes_its_pages(examiner: Client, exam: Exam) -> None:
    services.close_exam(exam)
    closed = version(exam)
    stream = events(examiner, f"{url('events', exam)}?version=0")

    async def delete_exam_and_read() -> bytes:
        await sync_to_async(services.delete_exam)(exam)
        return await anext(stream)

    assert read_first(stream, delete_exam_and_read) == (
        changed_event(closed),
        changed_event(closed + 1),
    )


def test_members_with_a_stream_open_count_as_seen(
    invigilator: Client, exam: Exam, settings: Settings
) -> None:
    settings.EXAMDESK_SEEN_EVERY = timedelta(milliseconds=20)
    settings.EXAMDESK_HEARTBEAT_EVERY = timedelta(milliseconds=10)
    ann = member(invigilator, exam)
    stream = events(invigilator, f"{url('events', exam)}?version=0")
    an_hour_ago = timezone.now() - timedelta(hours=1)

    async def seen_again() -> bool:
        await Member.objects.filter(pk=ann.pk).aupdate(last_seen=an_hour_ago)
        await asyncio.sleep(0.2)
        return (await Member.objects.aget(pk=ann.pk)).online

    assert read_first(stream, seen_again) == (b"data:\n\n", True)
