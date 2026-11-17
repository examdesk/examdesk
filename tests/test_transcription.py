"""Transcribing photographed questions with the exam's own AI service."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from pytest_django.fixtures import Settings

from examdesk import services
from examdesk.models import Exam
from tests.helpers import url

pytestmark = pytest.mark.django_db

SERVICE = {"base_url": "https://ai.example.edu/v1", "model": "vision", "api_key": "sk-1"}


class FakeService(ThreadingHTTPServer):
    """An OpenAI-compatible service on localhost that answers with `status`."""

    status = 200
    requests: list[tuple[dict[str, str], dict[str, Any]]]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/v1"


class Handler(BaseHTTPRequestHandler):
    server: FakeService

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(({k.lower(): v for k, v in self.headers.items()}, body))
        message = {"role": "assistant", "content": " Can we assume x > 0?\n"}
        reply = {
            "id": "1",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        }
        data = json.dumps(reply if self.server.status == 200 else {"error": {}}).encode()
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def ai(exam: Exam) -> Iterator[FakeService]:
    """The exam's AI service."""
    with FakeService(("127.0.0.1", 0), Handler) as server:
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        services.set_ai_service(exam, **SERVICE | {"base_url": server.base_url})
        yield server
        server.shutdown()


def transcribe(client: Client, exam: Exam, jpeg: bytes) -> tuple[int, bytes]:
    photo = SimpleUploadedFile("photo.jpg", jpeg, content_type="image/jpeg")
    response = client.post(url("invigilator:transcribe_photo", exam), {"photo": photo})
    return response.status_code, response.content


def step_two(client: Client, exam: Exam) -> str:
    query = {"venue": "LT19", "seat": "B14", "label": "Q3b"}
    return client.get(url("invigilator:report", exam), query).content.decode()


def saved(exam: Exam) -> Exam:
    return Exam.objects.get(pk=exam.pk)


@pytest.mark.django_db(transaction=True)
def test_transcribes_with_the_exams_service(
    invigilator: Client, exam: Exam, jpeg: bytes, ai: FakeService
) -> None:
    assert transcribe(invigilator, exam, jpeg) == (200, b"Can we assume x > 0?")
    [(headers, body)] = ai.requests
    assert headers["authorization"] == "Bearer sk-1"
    assert body["model"] == "vision"
    image = body["messages"][0]["content"][0]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,")
    assert "data-transcribe=" in step_two(invigilator, exam)


@pytest.mark.django_db(transaction=True)
def test_failed_transcription_is_bad_gateway(
    invigilator: Client, exam: Exam, jpeg: bytes, ai: FakeService
) -> None:
    ai.status = 400
    assert transcribe(invigilator, exam, jpeg)[0] == 502


def test_no_transcription_without_a_service(invigilator: Client, exam: Exam, jpeg: bytes) -> None:
    assert transcribe(invigilator, exam, jpeg)[0] == 404
    assert "data-transcribe=" not in step_two(invigilator, exam)


def test_examiner_sets_the_service_and_never_sees_the_key(examiner: Client, exam: Exam) -> None:
    response = examiner.post(url("examiner:ai_service", exam), SERVICE)
    assert response["Location"] == url("examiner:settings", exam)
    assert (saved(exam).ai_base_url, saved(exam).ai_api_key) == (SERVICE["base_url"], "sk-1")
    page = examiner.get(url("examiner:settings", exam)).content.decode()
    assert "sk-1" not in page
    assert "Saved; leave blank to keep" in page


def test_blank_key_is_kept_only_for_the_same_address(examiner: Client, exam: Exam) -> None:
    services.set_ai_service(exam, **SERVICE)
    examiner.post(url("examiner:ai_service", exam), SERVICE | {"model": "other", "api_key": ""})
    assert (saved(exam).ai_model, saved(exam).ai_api_key) == ("other", "sk-1")
    elsewhere = SERVICE | {"base_url": "https://elsewhere.example/v1", "api_key": ""}
    response = examiner.post(url("examiner:ai_service", exam), elsewhere)
    assert "Enter the API key." in response.content.decode()
    assert saved(exam).ai_base_url == SERVICE["base_url"]


def test_remove_clears_the_service(examiner: Client, exam: Exam) -> None:
    services.set_ai_service(exam, **SERVICE)
    examiner.post(url("examiner:ai_service", exam), {"remove": ""})
    assert not saved(exam).has_ai_service
    assert saved(exam).ai_api_key == ""


def test_service_needs_https(examiner: Client, exam: Exam) -> None:
    data = SERVICE | {"base_url": "http://localhost:11434/v1"}
    response = examiner.post(url("examiner:ai_service", exam), data)
    assert response.status_code == 400
    assert "Use an https:// address." in response.content.decode()
    assert not saved(exam).has_ai_service


def test_key_is_encrypted_and_survives_secret_key_rotation(exam: Exam, settings: Settings) -> None:
    services.set_ai_service(exam, **SERVICE)
    with connection.cursor() as cursor:
        cursor.execute("SELECT ai_api_key FROM examdesk_exam WHERE id = %s", [exam.pk])
        [stored] = cursor.fetchone()
    assert "sk-1" not in stored
    settings.SECRET_KEY_FALLBACKS = [settings.SECRET_KEY]
    settings.SECRET_KEY = "new secret key"
    assert saved(exam).ai_api_key == "sk-1"
    settings.SECRET_KEY_FALLBACKS = []
    assert saved(exam).ai_api_key == ""
