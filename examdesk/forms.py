from collections.abc import Iterable
from typing import Any

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest

from .labels import normalize_label, normalize_name, normalize_place
from .models import Clarification, Exam


def form_errors(form: forms.Form) -> str:
    return " ".join(str(error) for errors in form.errors.values() for error in errors)


class BaseForm(forms.Form):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


class PlaceField(forms.CharField):
    def to_python(self, value: Any) -> str:
        return normalize_place(super().to_python(value) or "")


class LabelField(forms.CharField):
    """Blank means `General`."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(max_length=30, required=False, **kwargs)

    def to_python(self, value: Any) -> str:
        try:
            return normalize_label(super().to_python(value) or "")
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error


class SignupForm(BaseForm):
    """Adds a name to allauth's sign-up form, which asks only for an email address."""

    name = forms.CharField(
        label="Your name",
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )

    def signup(self, request: HttpRequest, user: User) -> None:
        user.first_name = self.cleaned_data["name"]
        user.save(update_fields=["first_name"])


class ExamForm(BaseForm):
    name = forms.CharField(max_length=200)


class NameForm(BaseForm):
    name = forms.CharField(
        max_length=100, widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "off"})
    )

    def clean_name(self) -> str:
        return normalize_name(self.cleaned_data["name"])


class VenueForm(BaseForm):
    venue = PlaceField(max_length=30, required=False)


type Attrs = dict[str, str | bool]


def datalist_input(name: str, attrs: Attrs | None = None) -> forms.TextInput:
    """A text input backed by one of the datalists in base.html."""
    return forms.TextInput(attrs={"list": name, "autocomplete": "off", **(attrs or {})})


class ReportStartForm(BaseForm):
    """Step 1 of a report: the seat and the question."""

    venue = PlaceField(
        max_length=30,
        widget=datalist_input(
            "venues", {"autocapitalize": "characters", "placeholder": "e.g. LT19"}
        ),
    )
    seat = PlaceField(
        max_length=30,
        widget=datalist_input(
            "seats", {"autocapitalize": "characters", "autofocus": True, "placeholder": "e.g. B14"}
        ),
    )
    label = LabelField(
        label="Question",
        widget=datalist_input("labels", {"placeholder": "e.g. 3b, 3(b)(ii) or General"}),
    )


class PhotoForm(BaseForm):
    photo = forms.ImageField()

    def clean_photo(self) -> UploadedFile[bytes] | None:
        photo: UploadedFile[bytes] | None = self.cleaned_data["photo"]
        if photo and (photo.size or 0) > settings.EXAMDESK_PHOTO_UPLOAD_MAX_BYTES:
            raise forms.ValidationError("That photo is too large.")
        return photo


class ReportForm(ReportStartForm, PhotoForm):
    client_key = forms.UUIDField()
    clarification = forms.IntegerField(required=False)
    text = forms.CharField(required=False, max_length=settings.EXAMDESK_TEXT_MAX)
    photo = forms.ImageField(required=False)


class AIServiceForm(BaseForm):
    base_url = forms.URLField(
        label="API base URL",
        assume_scheme="https",
        widget=forms.URLInput(attrs={"placeholder": "https://…/v1"}),
    )
    model = forms.CharField(max_length=200, label="Model")
    api_key = forms.CharField(
        label="API key",
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
    )

    def __init__(self, *args: Any, exam: Exam, **kwargs: Any) -> None:
        initial = {"base_url": exam.ai_base_url, "model": exam.ai_model}
        super().__init__(*args, initial=initial, **kwargs)
        self.exam = exam
        if exam.ai_api_key:
            self.fields["api_key"].widget.attrs["placeholder"] = "Saved; leave blank to keep"

    def clean_base_url(self) -> str:
        url: str = self.cleaned_data["base_url"]
        if not url.startswith("https://"):
            raise forms.ValidationError("Use an https:// address.")
        return url

    def clean(self) -> dict[str, Any]:
        """A blank key keeps the saved one, for the same address only."""
        data = super().clean() or {}
        if not data.get("api_key") and data.get("base_url") == self.exam.ai_base_url:
            data["api_key"] = self.exam.ai_api_key
        if not data.get("api_key"):
            self.add_error("api_key", "Enter the API key.")
        return data


class ClarificationFilterForm(BaseForm):
    label = forms.MultipleChoiceField(
        required=False,
        widget=forms.SelectMultiple(attrs={"aria-label": "Questions", "placeholder": "Questions"}),
    )
    state = forms.ChoiceField(
        choices=[*Clarification.State.choices, ("all", "All")],
        required=False,
        widget=forms.Select(attrs={"aria-label": "State"}),
    )
    sort = forms.ChoiceField(required=False, widget=forms.HiddenInput)

    def __init__(
        self, *args: Any, labels: Iterable[str], sorts: Iterable[str], **kwargs: Any
    ) -> None:
        """`sorts` are column names; `-` before one reverses it."""
        super().__init__(*args, **kwargs)
        self.fields["label"].choices = [(label, label) for label in labels]  # type: ignore[attr-defined]
        self.fields["sort"].choices = [  # type: ignore[attr-defined]
            (f"{sign}{key}", key) for key in sorts for sign in ("", "-")
        ]


class TextForm(BaseForm):
    text = forms.CharField(max_length=settings.EXAMDESK_TEXT_MAX)

    def __init__(self, *args: Any, optional: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["text"].required = not optional


class AnswerForm(TextForm):
    expected_answer = forms.IntegerField(required=False)


class LabelForm(BaseForm):
    label = LabelField(label="Question", widget=datalist_input("labels", {"required": True}))


class DeleteExamForm(BaseForm):
    name = forms.CharField(
        label="Type the exam name to confirm",
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )

    def __init__(self, *args: Any, exam_name: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.exam_name = exam_name
        self.fields["name"].widget.attrs["placeholder"] = exam_name

    def clean_name(self) -> str:
        name: str = self.cleaned_data["name"]
        if name != self.exam_name:
            raise forms.ValidationError("That is not the exam name.")
        return name
