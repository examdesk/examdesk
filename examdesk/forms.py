from collections.abc import Iterable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest
from timezone_field import TimeZoneFormField

from . import pdf
from .labels import GENERAL, natural, normalize_label, normalize_name, normalize_place
from .models import Clarification, Exam, Report, validate_question_numbers


def form_errors(form: forms.Form) -> str:
    return " ".join(str(error) for errors in form.errors.values() for error in errors)


class BaseForm(forms.Form):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)


class PlaceField(forms.CharField):
    def to_python(self, value: Any) -> str:
        return normalize_place(super().to_python(value) or "")


class VenuesField(forms.Field):
    widget = forms.SelectMultiple

    def to_python(self, value: Any) -> list[str]:
        venues = {normalize_place(venue) for venue in value or []} - {""}
        if too_long_venue := next((venue for venue in venues if len(venue) > 30), None):
            raise forms.ValidationError(f"Too long: {too_long_venue}. Use up to 30 characters.")
        return sorted(venues, key=natural)


class LabelField(forms.CharField):
    """Blank means `General`."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(max_length=30, required=False, **kwargs)
        self.choices: list[str] = []

    def restrict_to(self, labels: Iterable[str]) -> None:
        """Allow only `labels` and `General`, picked from a list."""
        self.choices = sorted({*labels, GENERAL}, key=natural)
        attrs = self.widget.attrs.copy()
        del attrs["list"], attrs["maxlength"]
        self.widget = forms.Select(attrs, [("", ""), *((label, label) for label in self.choices)])

    def to_python(self, value: Any) -> str:
        try:
            return normalize_label(super().to_python(value) or "")
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

    def validate(self, value: Any) -> None:
        super().validate(value)
        if self.choices and value not in self.choices:
            raise forms.ValidationError("Pick a question from the list.")


class BaseExamForm(BaseForm):
    """A form with a `venue` or a `label`, picked from the exam's venues or question numbers when
    they are locked."""

    def __init__(self, *args: Any, exam: Exam, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if exam.venues_locked and "venue" in self.fields:
            attrs = self.fields["venue"].widget.attrs
            self.fields["venue"] = forms.ChoiceField(
                choices=[("", ""), *((venue, venue) for venue in exam.venues)],
                error_messages={"invalid_choice": "Pick a venue from the list."},
                widget=forms.Select(
                    {
                        name: value
                        for name, value in attrs.items()
                        if name not in ("list", "maxlength")
                    }
                ),
            )
        if exam.question_numbers_locked and "label" in self.fields:
            self.fields["label"].restrict_to(exam.question_labels)  # type: ignore[attr-defined]


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
    time_zone = forms.CharField(required=False)

    def clean_time_zone(self) -> ZoneInfo:
        """The browser's time zone, or the site's if this server doesn't know it."""
        time_zone: str = self.cleaned_data["time_zone"]
        return ZoneInfo(time_zone if time_zone in available_timezones() else settings.TIME_ZONE)


class ExamSettingsForm(BaseForm):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    time_zone = TimeZoneFormField(label="Time zone")
    question_numbers = forms.CharField(
        max_length=500,
        required=False,
        validators=[validate_question_numbers],
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "e.g. 1-40 or 1a-c, 2",
                "aria-describedby": "question-numbers-preview",
            }
        ),
    )
    question_numbers_locked = forms.BooleanField(
        label="Allow only these",
        required=False,
        widget=forms.CheckboxInput(
            attrs={"class": "checkbox checkbox-sm checkbox-primary rounded-sm"}
        ),
    )
    venues = VenuesField(
        required=False,
        widget=forms.SelectMultiple(
            attrs={
                "placeholder": "e.g. HALL1",
                "autocapitalize": "characters",
                "data-create-places": True,
            }
        ),
    )
    venues_locked = forms.BooleanField(
        label="Allow only these",
        required=False,
        widget=forms.CheckboxInput(
            attrs={"class": "checkbox checkbox-sm checkbox-primary rounded-sm"}
        ),
    )

    def __init__(self, *args: Any, exam: Exam, **kwargs: Any) -> None:
        """Suggests the venues that reports came from. The field's own values are options too: a
        select drops values it has no option for."""
        super().__init__(*args, **kwargs)
        report_venues = Report.objects.of_exam(exam).values_list("venue", flat=True)
        venues = sorted({*report_venues, *(self["venues"].value() or [])}, key=natural)
        self.fields["venues"].widget.choices = [(venue, venue) for venue in venues]

    def clean_question_numbers_locked(self) -> bool:
        """Without question numbers, nothing is locked."""
        locked: bool = self.cleaned_data["question_numbers_locked"]
        return locked and bool(self.cleaned_data.get("question_numbers"))

    def clean_venues_locked(self) -> bool:
        """Without venues, nothing is locked."""
        locked: bool = self.cleaned_data["venues_locked"]
        return locked and bool(self.cleaned_data.get("venues"))


class NameForm(BaseForm):
    name = forms.CharField(
        max_length=100, widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "off"})
    )

    def clean_name(self) -> str:
        return normalize_name(self.cleaned_data["name"])


type Attrs = dict[str, str | bool]


def datalist_input(name: str, attrs: Attrs | None = None) -> forms.TextInput:
    """A text input backed by one of the datalists in base.html."""
    return forms.TextInput(attrs={"list": name, "autocomplete": "off", **(attrs or {})})


class VenueForm(BaseExamForm):
    venue = PlaceField(
        max_length=30,
        required=False,
        widget=datalist_input(
            "venues",
            {
                "aria-label": "Venue",
                "autocapitalize": "characters",
                "placeholder": "e.g. HALL1",
                "required": True,
            },
        ),
    )


class ReportStartForm(BaseExamForm):
    """Step 1 of a report: the seat and the question."""

    venue = PlaceField(
        max_length=30,
        widget=datalist_input(
            "venues", {"autocapitalize": "characters", "placeholder": "e.g. HALL1"}
        ),
    )
    seat = PlaceField(
        max_length=30,
        widget=datalist_input(
            "seats", {"autocapitalize": "characters", "autofocus": True, "placeholder": "e.g. 14"}
        ),
    )
    label = LabelField(
        label="Question",
        widget=datalist_input("labels", {"placeholder": "e.g. 3b"}),
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


class AIConnectForm(BaseForm):
    base_url = forms.URLField(
        label="API base URL",
        assume_scheme="https",
        widget=forms.URLInput(attrs={"placeholder": "https://…/v1"}),
    )
    api_key = forms.CharField(
        label="API key", widget=forms.PasswordInput(attrs={"autocomplete": "off"})
    )

    def clean_base_url(self) -> str:
        url: str = self.cleaned_data["base_url"]
        if not url.startswith("https://"):
            raise forms.ValidationError("Use an https:// address.")
        return url


class AIModelsForm(BaseForm):
    vision_model = forms.ChoiceField(label="Vision")
    text_model = forms.ChoiceField(label="Text")

    def __init__(self, *args: Any, exam: Exam, **kwargs: Any) -> None:
        initial = {"vision_model": exam.ai_vision_model, "text_model": exam.ai_text_model}
        super().__init__(*args, initial=initial, **kwargs)
        choices = [("", "Pick a model…"), *((model, model) for model in exam.ai_models)]
        for name in ("vision_model", "text_model"):
            self.fields[name].choices = choices  # type: ignore[attr-defined]


class ExamFileForm(BaseForm):
    file = forms.FileField()

    def clean_file(self) -> UploadedFile[bytes]:
        upload: UploadedFile[bytes] = self.cleaned_data["file"]
        if (upload.size or 0) > settings.EXAMDESK_FILE_UPLOAD_MAX_BYTES:
            raise forms.ValidationError("That file is too large.")
        data = upload.read()
        upload.seek(0)
        match Path(upload.name or "").suffix.lower():
            case ".pdf":
                try:
                    pdf.page_count(data)
                except pdf.PdfError as error:
                    raise forms.ValidationError("Couldn't read that PDF.") from error
            case ".txt" | ".md":
                try:
                    text = data.decode()
                except UnicodeDecodeError as error:
                    raise forms.ValidationError("Save the file as UTF-8 text.") from error
                if not text.strip():
                    raise forms.ValidationError("That file has no text.")
            case _:
                raise forms.ValidationError("Upload a PDF or a text file.")
        return upload


class ClarificationFilterForm(BaseForm):
    STATES = {
        Clarification.State.OPEN: "Open",
        Clarification.State.ANSWERED: "Answered",
        "delivered": "Delivered",
        "closed": "Closed",
    }

    label = forms.MultipleChoiceField(
        required=False,
        widget=forms.SelectMultiple(
            attrs={"aria-label": "Questions", "placeholder": "All questions"}
        ),
    )
    state = forms.ChoiceField(
        choices=STATES,
        required=False,
        widget=forms.HiddenInput(attrs={"form": "queue-filters"}),
    )
    sort = forms.ChoiceField(
        choices={
            "wait": "Longest wait",
            "seats": "Most seats",
            "question": "Question",
            "-wait": "Newest",
        },
        required=False,
        widget=forms.Select(attrs={"aria-label": "Sort"}),
    )

    def __init__(self, *args: Any, labels: Iterable[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["label"].choices = [(label, label) for label in labels]  # type: ignore[attr-defined]


class TextForm(BaseForm):
    text = forms.CharField(max_length=settings.EXAMDESK_TEXT_MAX)

    def __init__(self, *args: Any, optional: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["text"].required = not optional


class AnswerForm(TextForm):
    expected_answer = forms.IntegerField(required=False)


class LabelForm(BaseExamForm):
    label = LabelField(
        label="Question",
        widget=datalist_input("labels", {"required": True, "placeholder": "e.g. 3b"}),
    )


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
