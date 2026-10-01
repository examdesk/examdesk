from typing import Any

from django.conf import settings
from django.http import HttpRequest


def config(request: HttpRequest) -> dict[str, Any]:
    return {
        "config": {
            "poll_every": int(settings.EXAMDESK_POLL_EVERY.total_seconds()),
            "offline_after": int(settings.EXAMDESK_OFFLINE_AFTER.total_seconds()),
            "photo_max_side": settings.EXAMDESK_PHOTO_MAX_SIDE,
            "photo_max_bytes": settings.EXAMDESK_PHOTO_MAX_BYTES,
            "backup_days": settings.EXAMDESK_BACKUP_DAYS,
            "text_max": settings.EXAMDESK_TEXT_MAX,
            "registration": settings.EXAMDESK_REGISTRATION,
            "registration_domains": settings.EXAMDESK_REGISTRATION_DOMAINS,
        }
    }
