import io
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client
from model_bakery import baker
from PIL import Image
from pytest_django.fixtures import Settings

from examdesk import services
from examdesk.models import Exam
from tests.helpers import open_link


@pytest.fixture(autouse=True)
def static_storage(settings: Settings) -> None:
    """Tests run without `collectstatic`, so without its manifest."""
    settings.STORAGES = settings.STORAGES | {
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
    }


@pytest.fixture
def staff() -> User:
    user: User = baker.make(User, username="lee", email="lee@example.edu", is_staff=True)
    return user


@pytest.fixture
def exam(staff: User) -> Exam:
    return services.create_exam(name="CS2109S Final", owner=staff)


@pytest.fixture
def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def media(settings: Settings, tmp_path: Path) -> Path:
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def invigilator(client: Client, exam: Exam) -> Client:
    open_link(client, exam.invigilator_token)
    return client


@pytest.fixture
def examiner(exam: Exam) -> Client:
    client = Client()
    open_link(client, exam.examiner_token, "Prof")
    return client
