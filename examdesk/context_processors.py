from typing import Any

from django.conf import settings
from django.http import HttpRequest


def config(request: HttpRequest) -> dict[str, Any]:
    return {
        "config": {
            "offline_after": int(settings.EXAMDESK_OFFLINE_AFTER.total_seconds()),
            "late_after": int(settings.EXAMDESK_LATE_AFTER.total_seconds()),
            "very_late_after": int(settings.EXAMDESK_VERY_LATE_AFTER.total_seconds()),
            "photo_max_side": settings.EXAMDESK_PHOTO_MAX_SIDE,
            "photo_max_bytes": settings.EXAMDESK_PHOTO_MAX_BYTES,
            "text_max": settings.EXAMDESK_TEXT_MAX,
            "registration": settings.EXAMDESK_REGISTRATION,
            "registration_domains": settings.EXAMDESK_REGISTRATION_DOMAINS,
        }
    }
