from base64 import urlsafe_b64encode
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models.expressions import Expression
from django.utils.crypto import salted_hmac


def _fernet() -> MultiFernet:
    """Encrypts with `SECRET_KEY`, and also decrypts with `SECRET_KEY_FALLBACKS`."""
    return MultiFernet(
        [
            Fernet(urlsafe_b64encode(salted_hmac(__name__, "", key, algorithm="sha256").digest()))
            for key in [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
        ]
    )


class EncryptedTextField(models.TextField):  # type: ignore[type-arg]
    """Text encrypted in the database. Text that no secret key decrypts reads as blank."""

    def from_db_value(
        self, value: str | None, expression: Expression, connection: BaseDatabaseWrapper
    ) -> str:
        if not value:
            return ""
        try:
            return _fernet().decrypt(value).decode()
        except InvalidToken:
            return ""

    def get_prep_value(self, value: Any) -> str:
        text: str = super().get_prep_value(value) or ""
        return _fernet().encrypt(text.encode()).decode() if text else ""
