"""Sign-in by emailed code, through django-allauth."""

from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.forms import LoginForm as BaseLoginForm
from allauth.account.models import EmailAddress
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.http import HttpRequest


def display_name(user: User) -> str:
    return user.get_full_name() or user.email or user.get_username()


class AccountAdapter(DefaultAccountAdapter):  # type: ignore[misc]  # allauth is untyped
    def add_message(self, request: HttpRequest, level: int, *args: Any, **kwargs: Any) -> None:
        """Only problems: the pages already show that a code went out or who signed in."""
        if level >= messages.WARNING:
            super().add_message(request, level, *args, **kwargs)

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return settings.EXAMDESK_REGISTRATION

    def clean_email(self, email: str) -> str:
        """Checked on sign-up, not sign-in, so existing accounts keep working."""
        email = super().clean_email(email)
        domains = settings.EXAMDESK_REGISTRATION_DOMAINS
        if domains and email.rpartition("@")[2].lower() not in domains:
            raise forms.ValidationError(f"Use an address at {', '.join(domains)}.")
        return email


class LoginForm(BaseLoginForm):  # type: ignore[misc]  # allauth is untyped
    def clean(self) -> dict[str, object]:
        """Give accounts made by `createsuperuser` or in the admin the EmailAddress that a
        code sign-in marks as verified; without it, they'd have to confirm the address again."""
        cleaned: dict[str, object] = super().clean()
        user = getattr(self, "user", None)
        if user is not None and user.email:
            EmailAddress.objects.get_or_create(
                user=user,
                email__iexact=user.email,
                defaults={
                    "email": user.email.lower(),
                    "primary": not EmailAddress.objects.filter(user=user).exists(),
                },
            )
        return cleaned
