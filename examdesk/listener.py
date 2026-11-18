"""Listening for new exam versions (migration 0007) and passing them to open subscriptions."""

import asyncio
import logging
from dataclasses import dataclass, field

import psycopg
from django.conf import settings
from django.db import connections
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Subscription:
    """A page's subscription to its exam. `versions` gets each new exam version, then None if
    listening stops."""

    exam_id: int
    member_id: int | None = None
    versions: asyncio.Queue[int | None] = field(default_factory=asyncio.Queue)


class Listener:
    """One connection per process listening for new exam versions. Passes them to subscriptions,
    and updates their members' `last_seen` every `EXAMDESK_SEEN_EVERY`."""

    def __init__(self) -> None:
        self._subscriptions: set[Subscription] = set()
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    def subscribe(self, exam_id: int) -> Subscription:
        """A subscription to the exam's versions. Await `ready` before reading the exam, and
        unsubscribe when done."""
        subscription = Subscription(exam_id)
        self._subscriptions.add(subscription)
        if self._task is None or self._task.done():
            self._ready = asyncio.Event()
            self._task = asyncio.create_task(self._listen())
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscriptions.discard(subscription)

    async def ready(self) -> None:
        await self._ready.wait()

    async def _listen(self) -> None:
        try:
            params = connections["default"].get_connection_params()
            for sync_only_param in ("context", "cursor_factory"):
                params.pop(sync_only_param, None)
            # TCP keepalives detect a connection that died without closing. Otherwise the
            # versions would stop silently.
            params |= {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10}
            async with await psycopg.AsyncConnection.connect(
                **params, autocommit=True
            ) as connection:
                await connection.execute("LISTEN examdesk_exam")
                self._ready.set()
                seen_every = settings.EXAMDESK_SEEN_EVERY.total_seconds()
                while True:
                    async for notification in connection.notifies(timeout=seen_every):
                        exam_id, version = map(int, notification.payload.split())
                        for subscription in self._subscriptions:
                            if subscription.exam_id == exam_id:
                                subscription.versions.put_nowait(version)
                    member_ids = [
                        subscription.member_id
                        for subscription in self._subscriptions
                        if subscription.member_id
                    ]
                    if member_ids:
                        await connection.execute(
                            "UPDATE examdesk_member SET last_seen = %s WHERE id = ANY(%s)",
                            [timezone.now(), member_ids],
                        )
        except Exception:
            logger.exception("Listening for exam versions failed")
        finally:
            self._ready.set()
            for subscription in self._subscriptions:
                subscription.versions.put_nowait(None)


listener = Listener()
