"""The exam's own AI service: transcribing photos, drafting answers, finding duplicates and
analysing the clarifications."""

import asyncio
import contextlib
import json
import math
import re
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.handlers.asgi import ASGIHandler
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import AsyncClient, Client
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from pytest_django.fixtures import Settings

from examdesk import ai as ai_service
from examdesk import services
from examdesk.models import Analysis, Exam, ExamFile, Report
from tests.helpers import named, report, url

pytestmark = pytest.mark.django_db

SERVICE = {"base_url": "https://ai.example.edu/v1", "api_key": "sk-1"}
MODELS = ["careful", "fast", "vision"]
CHOSEN_MODELS = {"vision_model": "vision", "fast_model": "fast", "careful_model": "careful"}


def connect(exam: Exam, base_url: str = SERVICE["base_url"]) -> None:
    services.connect_ai_service(exam, base_url=base_url, api_key="sk-1", models=MODELS)
    services.choose_ai_models(exam, **CHOSEN_MODELS)


class FakeService(ThreadingHTTPServer):
    """An OpenAI-compatible service on localhost that answers with `status` and `content`. With
    `never_answers` it doesn't answer, and sets `client_disconnected` once the client hangs up."""

    status = 200
    content = " Can we assume x > 0?\n"
    delay = 0.0
    logprobs: list[dict[str, Any]] | None = None
    never_answers = False
    client_disconnected: threading.Event
    requests: list[tuple[dict[str, str], dict[str, Any]]]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/v1"


class Handler(BaseHTTPRequestHandler):
    server: FakeService

    def do_GET(self) -> None:
        models = [{"id": id, "object": "model", "created": 0, "owned_by": "x"} for id in MODELS]
        self.send({"object": "list", "data": models[::-1]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(({k.lower(): v for k, v in self.headers.items()}, body))
        if self.server.never_answers:
            self.connection.recv(1)  # returns when the client disconnects
            self.server.client_disconnected.set()
            return
        time.sleep(self.server.delay)
        message = {"role": "assistant", "content": self.server.content}
        choice = {"index": 0, "finish_reason": "stop", "message": message}
        if self.server.logprobs is not None:
            choice["logprobs"] = {"content": self.server.logprobs}
        self.send(
            {
                "id": "1",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [choice],
            }
        )

    def send(self, reply: dict[str, Any]) -> None:
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
    """The exam's AI service, with an empty cache."""
    cache.clear()
    with FakeService(("127.0.0.1", 0), Handler) as server:
        server.requests = []
        server.client_disconnected = threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        connect(exam, server.base_url)
        yield server
        server.shutdown()


def transcribe(client: Client, exam: Exam, jpeg: bytes) -> tuple[int, bytes]:
    photo = SimpleUploadedFile("photo.jpg", jpeg, content_type="image/jpeg")
    response = client.post(url("invigilator:transcribe_photo", exam), {"photo": photo})
    return response.status_code, response.content


def step_two(client: Client, exam: Exam) -> str:
    query = {"venue": "HALL1", "seat": "14", "label": "Q3b"}
    return client.get(url("invigilator:report", exam), query).content.decode()


def saved(exam: Exam) -> Exam:
    return Exam.objects.get(pk=exam.pk)


def chosen_models(exam: Exam) -> tuple[str, str, str]:
    models = Exam.objects.values_list("ai_vision_model", "ai_fast_model", "ai_careful_model")
    return models.get(pk=exam.pk)


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


@pytest.mark.django_db(transaction=True)
def test_a_cancelled_transcription_stops_asking_the_service(
    invigilator: Client, exam: Exam, jpeg: bytes, ai: FakeService
) -> None:
    ai.never_answers = True
    invigilator.get(url("invigilator:report", exam))  # sets the CSRF cookie
    photo = SimpleUploadedFile("photo.jpg", jpeg, content_type="image/jpeg")
    body = encode_multipart(BOUNDARY, {"photo": photo})
    cookies = "; ".join(f"{name}={cookie.value}" for name, cookie in invigilator.cookies.items())
    scope = {
        "type": "http",
        "method": "POST",
        "path": url("invigilator:transcribe_photo", exam),
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", MULTIPART_CONTENT.encode()),
            (b"content-length", str(len(body)).encode()),
            (b"cookie", cookies.encode()),
            (b"x-csrftoken", invigilator.cookies["csrftoken"].value.encode()),
        ],
    }

    async def post_then_disconnect() -> bool:
        communicator = ApplicationCommunicator(ASGIHandler(), scope)
        await communicator.send_input({"type": "http.request", "body": body})
        while not ai.requests:
            await asyncio.sleep(0.01)
        await communicator.send_input({"type": "http.disconnect"})
        await communicator.wait()
        return await asyncio.to_thread(ai.client_disconnected.wait, 5)

    with ThreadPoolExecutor(1) as executor:  # Playwright's tests leave a loop running in this one
        assert executor.submit(asyncio.run, post_then_disconnect()).result()


def test_no_transcription_without_a_service(invigilator: Client, exam: Exam, jpeg: bytes) -> None:
    assert transcribe(invigilator, exam, jpeg)[0] == 404
    assert "data-transcribe=" not in step_two(invigilator, exam)


def test_lists_the_services_models(ai: FakeService) -> None:
    assert ai_service.list_models(ai.base_url, "sk-1") == MODELS


def connect_to_service_listing(
    client: Client, exam: Exam, monkeypatch: pytest.MonkeyPatch, models: list[str]
) -> str:
    """The settings page after connecting to a service that lists `models` and gives logprobs."""
    monkeypatch.setattr(ai_service, "list_models", lambda base_url, api_key: models)
    monkeypatch.setattr(ai_service, "fast_model_returns_logprobs", lambda exam: True)
    response = client.post(url("examiner:connect_ai_service", exam), SERVICE, follow=True)
    assert response.redirect_chain == [(url("examiner:settings", exam), 302)]
    return response.content.decode()


def test_examiner_connects_then_picks_models(
    examiner: Client, exam: Exam, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = connect_to_service_listing(examiner, exam, monkeypatch, MODELS)
    assert (saved(exam).ai_api_key, saved(exam).ai_models) == ("sk-1", MODELS)
    assert chosen_models(exam) == ("", "", "")
    assert page.count('<option value="" selected>Pick a model…</option>') == 3
    assert "Connected" in page
    for label, hint in [
        ("Vision model", "Reads photos and PDFs"),
        ("Fast model", "Checks possible duplicates"),
        ("Careful model", "Drafts answers"),
    ]:
        assert f">{label}</label>" in page
        assert f">{hint}</div>" in page
    assert '<option value="vision">vision</option>' in page
    assert "sk-1" not in page
    assert "Files for the AI" not in page
    examiner.post(url("examiner:choose_ai_models", exam), {"vision_model": "vision"})
    assert chosen_models(exam) == ("vision", "", "")
    examiner.post(url("examiner:choose_ai_models", exam), CHOSEN_MODELS)
    assert chosen_models(exam) == ("vision", "fast", "careful")
    assert "Files for the AI" in examiner.get(url("examiner:settings", exam)).content.decode()
    examiner.post(url("examiner:choose_ai_models", exam), CHOSEN_MODELS | {"careful_model": ""})
    assert chosen_models(exam) == ("vision", "fast", "")
    assert "Files for the AI" not in examiner.get(url("examiner:settings", exam)).content.decode()


def test_one_listed_model_does_everything(
    examiner: Client, exam: Exam, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = connect_to_service_listing(examiner, exam, monkeypatch, ["qwen"])
    assert chosen_models(exam) == ("qwen", "qwen", "qwen")
    assert '<span class="truncate text-muted">qwen</span>' in page
    assert "models-form" not in page
    assert "Files for the AI" in page


def test_only_listed_models_can_be_picked(examiner: Client, exam: Exam) -> None:
    connect(exam)
    response = examiner.post(
        url("examiner:choose_ai_models", exam), CHOSEN_MODELS | {"vision_model": "gpt"}
    )
    assert response.status_code == 400
    assert chosen_models(exam) == ("vision", "fast", "careful")


def save_fast_model(client: Client, exam: Exam, model: str) -> str:
    response = client.post(
        url("examiner:choose_ai_models", exam), CHOSEN_MODELS | {"fast_model": model}, follow=True
    )
    return response.content.decode()


NO_LOGPROBS_WARNING = "With careful, every possible duplicate shows 100% match."


def test_warns_when_the_fast_model_gives_no_logprobs(
    examiner: Client, exam: Exam, ai: FakeService
) -> None:
    page = save_fast_model(examiner, exam, "careful")
    assert "Saved the models." in page
    assert NO_LOGPROBS_WARNING in page
    assert chosen_models(exam) == ("vision", "careful", "careful")
    [(_, body)] = ai.requests
    assert (body["model"], body["logprobs"]) == ("careful", True)


def test_no_warning_when_the_fast_model_gives_logprobs(
    examiner: Client, exam: Exam, ai: FakeService
) -> None:
    ai.logprobs = reply_line_tokens(0.9)
    assert NO_LOGPROBS_WARNING not in save_fast_model(examiner, exam, "careful")


@pytest.mark.parametrize(("status", "warns"), [(400, True), (404, False)])
def test_only_a_refused_logprobs_request_warns(
    examiner: Client, exam: Exam, ai: FakeService, status: int, warns: bool
) -> None:
    ai.status = status
    page = save_fast_model(examiner, exam, "careful")
    assert (NO_LOGPROBS_WARNING in page) == warns
    assert chosen_models(exam) == ("vision", "careful", "careful")


def test_an_unchanged_fast_model_is_not_tested_again(
    examiner: Client, exam: Exam, ai: FakeService
) -> None:
    save_fast_model(examiner, exam, "fast")
    assert ai.requests == []


def test_connecting_warns_when_the_fast_model_gives_no_logprobs(
    examiner: Client, exam: Exam, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_service, "list_models", lambda base_url, api_key: ["qwen"])
    monkeypatch.setattr(ai_service, "fast_model_returns_logprobs", lambda exam: False)
    response = examiner.post(url("examiner:connect_ai_service", exam), SERVICE, follow=True)
    assert "With qwen, every possible duplicate shows 100% match." in response.content.decode()
    assert chosen_models(exam) == ("qwen", "qwen", "qwen")


@pytest.mark.parametrize("model", list(CHOSEN_MODELS))
def test_each_feature_needs_only_its_model(
    examiner: Client, invigilator: Client, exam: Exam, jpeg: bytes, model: str
) -> None:
    connect(exam)
    services.choose_ai_models(
        exam, **dict.fromkeys(CHOSEN_MODELS, "") | {model: CHOSEN_MODELS[model]}
    )
    clarification = report(exam).clarification
    page = examiner.get(url("examiner:clarification", exam, clarification.pk)).content.decode()
    settings_page = examiner.get(url("examiner:settings", exam)).content.decode()
    assert ("data-transcribe=" in step_two(invigilator, exam)) == (model == "vision_model")
    duplicates_url = url("examiner:clarification_duplicates", exam, clarification.pk)
    assert (duplicates_url in page) == (model == "fast_model")
    assert ("data-draft=" in page) == (model == "careful_model")
    assert ("Files for the AI" in settings_page) == (model == "careful_model")
    assert (url("examiner:analysis", exam) in settings_page) == (model == "careful_model")
    if model != "vision_model":
        assert transcribe(invigilator, exam, jpeg)[0] == 404
    if model != "fast_model":
        assert duplicates(examiner, exam)[0] == 404
    if model != "careful_model":
        assert draft(examiner, exam, clarification.pk)[0] == 404


@pytest.mark.django_db(transaction=True)
def test_migration_keeps_the_text_model_as_the_fast_and_careful_models() -> None:
    previous_migration = [("examdesk", "0011_exam_venues")]
    executor = MigrationExecutor(connection)
    executor.migrate(previous_migration)
    apps = executor.loader.project_state(previous_migration).apps
    owner = apps.get_model("auth", "User").objects.create(username="owner")
    apps.get_model("examdesk", "Exam").objects.create(
        name="Algorithms Final",
        owner=owner,
        time_zone="UTC",
        ai_vision_model="vision",
        ai_text_model="text",
    )
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
    assert chosen_models(Exam.objects.get()) == ("vision", "text", "text")


def test_refused_key_is_an_error_on_the_key(
    examiner: Client, exam: Exam, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(base_url: str, api_key: str) -> list[str]:
        response = httpx2.Response(401, request=httpx2.Request("GET", base_url))
        raise openai.AuthenticationError("refused", response=response, body=None)

    monkeypatch.setattr(ai_service, "list_models", refuse)
    response = examiner.post(url("examiner:connect_ai_service", exam), SERVICE)
    assert response.status_code == 400
    assert "The service refused that key." in response.content.decode()
    assert saved(exam).ai_base_url == ""


def test_disconnect_clears_the_service(examiner: Client, exam: Exam) -> None:
    connect(exam)
    examiner.post(url("examiner:disconnect_ai_service", exam))
    assert (saved(exam).ai_base_url, saved(exam).ai_api_key, saved(exam).ai_models) == ("", "", [])
    assert chosen_models(exam) == ("", "", "")


def test_service_needs_https(examiner: Client, exam: Exam) -> None:
    data = SERVICE | {"base_url": "http://localhost:11434/v1"}
    response = examiner.post(url("examiner:connect_ai_service", exam), data)
    assert response.status_code == 400
    assert "Use an https:// address." in response.content.decode()
    assert saved(exam).ai_base_url == ""


def test_key_is_encrypted_and_survives_secret_key_rotation(exam: Exam, settings: Settings) -> None:
    connect(exam)
    with connection.cursor() as cursor:
        cursor.execute("SELECT ai_api_key FROM examdesk_exam WHERE id = %s", [exam.pk])
        [stored] = cursor.fetchone()
    assert "sk-1" not in stored
    settings.SECRET_KEY_FALLBACKS = [settings.SECRET_KEY]
    settings.SECRET_KEY = "new secret key"
    assert saved(exam).ai_api_key == "sk-1"
    settings.SECRET_KEY_FALLBACKS = []
    assert saved(exam).ai_api_key == ""


def pdf(pages: int) -> bytes:
    """A PDF of blank pages."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
        *[b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"] * pages,
    ]
    data, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data += b"%d 0 obj\n%b\nendobj\n" % (number, body)
    xref = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    data += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return data


def upload(client: Client, exam: Exam, name: str, data: bytes) -> str:
    file = SimpleUploadedFile(name, data)
    response = client.post(url("examiner:add_file", exam), {"file": file}, follow=True)
    return response.content.decode()


def draft(client: Client, exam: Exam, clarification_id: int) -> tuple[int, bytes]:
    response = client.post(url("examiner:draft_answer", exam, clarification_id))
    return response.status_code, response.content


@pytest.mark.django_db(transaction=True)
def test_drafts_from_the_exams_files(examiner: Client, exam: Exam, ai: FakeService) -> None:
    upload(examiner, exam, "errata.txt", b"Q3b: x is a positive integer.")
    clarification = report(exam).clarification
    page = examiner.get(url("examiner:clarification", exam, clarification.pk)).content.decode()
    assert "data-draft=" in page
    assert draft(examiner, exam, clarification.pk) == (200, b"Can we assume x > 0?")
    [(_, body)] = ai.requests
    assert body["model"] == "careful"
    system, question = (message["content"] for message in body["messages"])
    assert "No comment." in system
    assert "# errata.txt\n\nQ3b: x is a positive integer." in system
    assert question == "Exam question: Q3b\nThe student asks:\nIs x an integer?"


@pytest.mark.django_db(transaction=True)
def test_failed_draft_is_bad_gateway(examiner: Client, exam: Exam, ai: FakeService) -> None:
    ai.status = 400
    assert draft(examiner, exam, report(exam).clarification_id)[0] == 502


def test_no_draft_without_a_service(examiner: Client, exam: Exam) -> None:
    clarification = report(exam).clarification
    assert draft(examiner, exam, clarification.pk)[0] == 404
    page = examiner.get(url("examiner:clarification", exam, clarification.pk)).content.decode()
    assert "data-draft=" not in page


def test_invigilators_cannot_draft(invigilator: Client, exam: Exam) -> None:
    connect(exam)
    assert draft(invigilator, exam, report(exam).clarification_id)[0] == 403


def report_with_photo(exam: Exam, jpeg: bytes, text: str = "") -> Report:
    return report(exam, text=text, photo=SimpleUploadedFile("p.jpg", jpeg, "image/jpeg"))


def transcribe_report_photo(client: Client, exam: Exam, report_id: int) -> tuple[int, bytes]:
    response = client.post(url("examiner:transcribe_photo", exam, report_id))
    return response.status_code, response.content


def clarification_page(client: Client, exam: Exam, clarification_id: int) -> str:
    return client.get(url("examiner:clarification", exam, clarification_id)).content.decode()


@pytest.mark.django_db(transaction=True)
def test_examiners_transcribe_the_question_photo(
    examiner: Client, exam: Exam, media: Path, jpeg: bytes, ai: FakeService
) -> None:
    photo_report = report_with_photo(exam, jpeg)
    transcribe_url = url("examiner:transcribe_photo", exam, photo_report.pk)
    page = clarification_page(examiner, exam, photo_report.clarification_id)
    assert f'data-transcribe-wording="{transcribe_url}"' in page
    result = transcribe_report_photo(examiner, exam, photo_report.pk)
    assert result == (200, b"Can we assume x > 0?")
    [(_, body)] = ai.requests
    assert body["model"] == "vision"
    image = body["messages"][0]["content"][0]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,")


def test_no_transcription_when_the_question_has_wording(
    examiner: Client, exam: Exam, media: Path, jpeg: bytes
) -> None:
    connect(exam)
    photo_report = report_with_photo(exam, jpeg, text="Is x an integer?")
    transcribe_url = url("examiner:transcribe_photo", exam, photo_report.pk)
    assert transcribe_url not in clarification_page(examiner, exam, photo_report.clarification_id)


@pytest.mark.django_db(transaction=True)
def test_failed_photo_transcription_is_bad_gateway(
    examiner: Client, exam: Exam, media: Path, jpeg: bytes, ai: FakeService
) -> None:
    ai.status = 400
    assert transcribe_report_photo(examiner, exam, report_with_photo(exam, jpeg).pk)[0] == 502


def test_no_photo_transcription_without_a_service(
    examiner: Client, exam: Exam, media: Path, jpeg: bytes
) -> None:
    photo_report = report_with_photo(exam, jpeg)
    assert transcribe_report_photo(examiner, exam, photo_report.pk)[0] == 404
    page = clarification_page(examiner, exam, photo_report.clarification_id)
    assert "data-transcribe-wording=" not in page


def test_only_reports_with_a_photo_are_transcribed(examiner: Client, exam: Exam) -> None:
    connect(exam)
    assert transcribe_report_photo(examiner, exam, report(exam).pk)[0] == 404


def test_invigilators_cannot_transcribe_a_reports_photo(
    invigilator: Client, exam: Exam, media: Path, jpeg: bytes
) -> None:
    connect(exam)
    assert transcribe_report_photo(invigilator, exam, report_with_photo(exam, jpeg).pk)[0] == 403


def read(client: Client, exam: Exam, file: ExamFile, number: int) -> int:
    return client.post(url("examiner:read_page", exam, file.pk, number)).status_code


def test_text_files_are_read_on_upload(examiner: Client, exam: Exam) -> None:
    connect(exam)
    page = upload(examiner, exam, "errata.md", b"Q3b: x is a positive integer.")
    assert "Added errata.md." in page
    assert "data-read-pages" not in page


def test_pdf_pages_wait_for_the_ai_to_read_them(
    examiner: Client, exam: Exam, media: Path, django_capture_on_commit_callbacks: Any
) -> None:
    connect(exam)
    page = upload(examiner, exam, "paper.pdf", pdf(2))
    [file] = exam.files.all()
    assert "Reading 0 of 2 pages" in page
    assert 'value="0" max="2" aria-label="Pages read"' in page
    assert url("examiner:read_page", exam, file.pk, 2) in page
    assert Path(file.pdf.path).exists()
    with django_capture_on_commit_callbacks(execute=True):
        examiner.post(url("examiner:delete_file", exam, file.pk))
    assert not exam.files.exists()
    assert not Path(file.pdf.path).exists()


@pytest.mark.django_db(transaction=True)
def test_reads_pdf_pages_with_the_service(
    examiner: Client, exam: Exam, media: Path, ai: FakeService
) -> None:
    upload(examiner, exam, "paper.pdf", pdf(2))
    [file] = exam.files.all()
    assert read(examiner, exam, file, 2) == 204
    [(_, body)] = ai.requests
    assert body["model"] == "vision"
    image, prompt = body["messages"][0]["content"]
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert "never put the text in a code block" in prompt["text"]
    page = examiner.get(url("examiner:settings", exam)).content.decode()
    assert "Reading 1 of 2 pages" in page
    read(examiner, exam, file, 1)
    page = examiner.get(url("examiner:settings", exam)).content.decode()
    assert "data-read-pages" not in page
    assert "2 pages</p>" in page


@pytest.mark.django_db(transaction=True)
def test_failed_page_stays_unread(
    examiner: Client, exam: Exam, media: Path, ai: FakeService
) -> None:
    upload(examiner, exam, "paper.pdf", pdf(1))
    ai.status = 400
    [file] = exam.files.all()
    assert read(examiner, exam, file, 1) == 502
    assert file.pages.get().is_read is False


def test_only_examiners_read_pages_with_a_service(
    examiner: Client, invigilator: Client, exam: Exam, media: Path
) -> None:
    connect(exam)
    upload(examiner, exam, "paper.pdf", pdf(1))
    [file] = exam.files.all()
    assert read(invigilator, exam, file, 1) == 403
    services.disconnect_ai_service(exam)
    assert read(examiner, exam, file, 1) == 404


@pytest.mark.parametrize(
    ("name", "data", "error"),
    [
        ("broken.pdf", b"not a pdf", "Couldn&#x27;t read that PDF."),
        ("notes.txt", b"\xff\xfe", "Save the file as UTF-8 text."),
        ("blank.txt", b"  \n", "That file has no text."),
        ("paper.docx", b"PK", "Upload a PDF or a text file."),
    ],
)
def test_unreadable_files_are_refused(
    examiner: Client, exam: Exam, name: str, data: bytes, error: str
) -> None:
    connect(exam)
    assert error in upload(examiner, exam, name, data)
    assert not exam.files.exists()


def test_examiner_sees_each_page_and_what_the_ai_read(
    examiner: Client, invigilator: Client, exam: Exam, media: Path
) -> None:
    connect(exam)
    upload(examiner, exam, "paper.pdf", pdf(2))
    [file] = exam.files.all()
    file.pages.filter(number=1).update(text="1A. Is $x$ an integer?", is_read=True)
    page = examiner.get(url("examiner:file", exam, file.pk)).content.decode()
    assert '1A. Is <span class="math inline"><math' in page
    assert '<p class="text-sm text-subtle">Not read yet</p>' in page
    assert "2 pages · 1 of 2 read" in page
    assert '<a href="#page-2" class="unread" title="Not read yet">2</a>' in page
    assert url("examiner:page_image", exam, file.pk, 2) in page
    image = examiner.get(url("examiner:page_image", exam, file.pk, 2))
    assert image["Content-Type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")
    assert examiner.get(url("examiner:page_image", exam, file.pk, 3)).status_code == 404
    assert invigilator.get(url("examiner:file", exam, file.pk)).status_code == 403


def test_pdfs_need_a_vision_model(examiner: Client, exam: Exam) -> None:
    connect(exam)
    services.choose_ai_models(exam, **CHOSEN_MODELS | {"vision_model": ""})
    page = upload(examiner, exam, "paper.pdf", pdf(1))
    assert "Pick a vision model to read PDFs." in page
    assert 'accept=".txt,.md"' in page
    assert not exam.files.exists()


def test_text_files_show_their_text_without_images(examiner: Client, exam: Exam) -> None:
    connect(exam)
    upload(examiner, exam, "errata.md", b"Q3b: x is a positive integer.")
    [file] = exam.files.all()
    page = examiner.get(url("examiner:file", exam, file.pk)).content.decode()
    assert "Q3b: x is a positive integer." in page
    assert "/image/" not in page


def three_clarifications(exam: Exam) -> None:
    for seat, label, text in [
        ("14", "Q3b", "Is the answer big-O or an exact count?"),
        ("15", "Q1", "Is the list sorted?"),
        ("16", "Q3b", "Do we give the exact count?"),
    ]:
        report(exam, seat=seat, label=label, text=text)


def duplicates(client: Client, exam: Exam) -> tuple[int, str]:
    """The duplicates panel of the latest Q3b clarification."""
    clarification = exam.clarifications.filter(label="Q3b").last()
    assert clarification is not None
    response = client.get(url("examiner:clarification_duplicates", exam, clarification.pk))
    return response.status_code, response.content.decode()


def reply_line_tokens(yes_probability: float) -> list[dict[str, Any]]:
    """The tokens of a reply line "C1: Yes" or "C1: No", with yes and no alternatives."""
    alternatives = [
        {"token": " Yes", "logprob": math.log(yes_probability)},
        {"token": " No", "logprob": math.log(1 - yes_probability)},
    ]
    word = " Yes" if yes_probability >= 0.5 else " No"
    return [
        {"token": "C1", "logprob": 0, "top_logprobs": []},
        {"token": ":", "logprob": 0, "top_logprobs": []},
        {"token": word, "logprob": 0, "top_logprobs": alternatives},
    ]


@pytest.mark.django_db(transaction=True)
def test_ai_confirms_possible_duplicates(examiner: Client, exam: Exam, ai: FakeService) -> None:
    three_clarifications(exam)
    later = exam.clarifications.last()
    assert later is not None
    page = examiner.get(url("examiner:clarification", exam, later.pk)).content.decode()
    assert page.count("% alike") == 1  # until the AI service replies
    ai.content = "C1: Yes\nC2: No"
    status, panel = duplicates(examiner, exam)
    assert status == 200
    assert panel.count("% match") == 1
    assert "100% match" in panel  # without logprobs
    assert "big-O" in panel
    [(_, body)] = ai.requests
    assert body["model"] == "fast"
    assert body["logprobs"]
    prompt = body["messages"][1]["content"]
    assert "Clarification:\n<clarification>\n[Q3b] Do we give the exact count?\n" in prompt
    assert '<clarification id="C1">\n[Q3b] Is the answer big-O or an exact count?\n' in prompt
    assert '<clarification id="C2">\n[Q1] Is the list sorted?\n' in prompt


@pytest.mark.django_db(transaction=True)
def test_duplicates_show_their_probability(examiner: Client, exam: Exam, ai: FakeService) -> None:
    three_clarifications(exam)
    ai.logprobs = reply_line_tokens(0.6) + reply_line_tokens(0.9)
    panel = duplicates(examiner, exam)[1]
    assert "60% match" in panel
    assert "90% match" in panel
    assert panel.index("Is the list sorted") < panel.index("big-O")  # likeliest first
    no_alternatives = {"token": " No", "logprob": 0, "top_logprobs": []}
    ai.logprobs = [*reply_line_tokens(0.6), *reply_line_tokens(0.9)[:2], no_alternatives]
    cache.clear()
    panel = duplicates(examiner, exam)[1]
    assert "big-O" in panel
    assert "Is the list sorted" not in panel


@pytest.mark.django_db(transaction=True)
def test_no_duplicates_when_the_ai_finds_none(
    examiner: Client, exam: Exam, ai: FakeService
) -> None:
    three_clarifications(exam)
    ai.content = "C1: No\nC2: No"
    assert duplicates(examiner, exam) == (200, "")


@pytest.mark.django_db(transaction=True)
def test_ai_is_not_asked_about_a_photo_without_wording(
    examiner: Client, exam: Exam, ai: FakeService, media: Path, jpeg: bytes
) -> None:
    report(exam)
    photo = SimpleUploadedFile("p.jpg", jpeg, "image/jpeg")
    report(exam, seat="21", text="", photo=photo)
    assert duplicates(examiner, exam) == (200, "")
    assert not ai.requests


@pytest.mark.django_db(transaction=True)
def test_replies_are_cached(examiner: Client, exam: Exam, ai: FakeService) -> None:
    three_clarifications(exam)
    ai.content = "C1: Yes\nC2: Yes"
    duplicates(examiner, exam)
    assert duplicates(examiner, exam)[1].count("% match") == 2
    assert len(ai.requests) == 1
    report(exam, seat="17", label="Q1", text="Is the input list sorted?")
    ai.content = "C1: Yes\nC2: Yes\nC3: Yes"
    duplicates(examiner, exam)
    assert len(ai.requests) == 2  # a new candidate


@pytest.mark.parametrize(("status", "content"), [(400, ""), (200, "Yes.")])
@pytest.mark.django_db(transaction=True)
def test_failed_ai_falls_back_to_the_most_alike(
    examiner: Client, exam: Exam, ai: FakeService, status: int, content: str
) -> None:
    three_clarifications(exam)
    ai.status, ai.content = status, content
    status, panel = duplicates(examiner, exam)
    assert status == 200
    assert "% alike" in panel
    assert "% match" not in panel


def test_no_ai_duplicates_without_a_service(examiner: Client, exam: Exam) -> None:
    three_clarifications(exam)
    assert duplicates(examiner, exam)[0] == 404


def test_invigilators_cannot_ask_for_duplicates(invigilator: Client, exam: Exam) -> None:
    three_clarifications(exam)
    connect(exam)
    assert duplicates(invigilator, exam)[0] == 403


FINDINGS = {
    "confusions": [
        {
            "title": "Big-O or an exact count",
            "questions": ["Q3b"],
            "description": "Two seats asked which form the answer takes.",
        }
    ],
    "unclear_wording": [
        {
            "question": "Q3b",
            "explanation": '"Count the comparisons" reads as an exact count or a bound.',
            "suggested_wording": "Give the number of comparisons in big-O notation.",
        }
    ],
    "errors": [],
    "inconsistent_answers": [
        {"question": "Q3b", "description": "The first answer asked for an exact count."}
    ],
}


def clarifications_in_every_state(exam: Exam) -> None:
    """Q3b answered and corrected for two seats, Q1 announced with a duplicate merged into it,
    Q2 open, and a general announcement."""
    prof = named(exam, "Prof")
    q3b = report(exam, seat="14", text="Is the answer big-O or an exact count?").clarification
    report(exam, seat="15", clarification=q3b, text="Do we give the exact count?")
    first_answer = services.answer_clarification(
        q3b, text="An exact count.", member=prof, expected_answer=None
    )
    services.answer_clarification(q3b, text="Big-O.", member=prof, expected_answer=first_answer.pk)
    q1 = report(exam, seat="16", label="Q1", text="Is the list sorted?").clarification
    duplicate = report(exam, seat="17", label="Q1", text="Sorted list?").clarification
    services.merge_clarifications(sources=[duplicate], target=q1)
    services.announce(prof, text="The list in Q1 is sorted.", clarification=q1)
    report(exam, seat="18", label="Q2", text="Can we use a calculator?")
    services.announce(prof, text="Ten minutes left.")


def analyse(client: Client, exam: Exam) -> str:
    response = client.post(url("examiner:analyse", exam), follow=True)
    assert response.redirect_chain == [(url("examiner:analysis", exam), 302)]
    return response.content.decode()


@pytest.mark.django_db(transaction=True)
def test_analyses_the_clarifications_after_the_exam(
    examiner: Client, exam: Exam, ai: FakeService
) -> None:
    clarifications_in_every_state(exam)
    upload(examiner, exam, "paper.txt", b"Q3b. Count the comparisons.")
    services.set_live(exam, live=False)
    page = examiner.get(url("examiner:analysis", exam)).content.decode()
    assert "No analysis yet" in page
    ai.content = json.dumps(FINDINGS)
    page = analyse(examiner, exam)
    assert "Big-O or an exact count" in page
    assert "Give the number of comparisons in big-O notation." in page
    assert page.count("None found") == 1
    assert "Generated " in page
    [(_, body)] = ai.requests
    assert body["model"] == "careful"
    system, clarifications = (message["content"] for message in body["messages"])
    assert system.endswith("# paper.txt\n\nQ3b. Count the comparisons.")
    assert clarifications == (
        '<clarification question="Q1" seats="2">\n'
        "Asked: Is the list sorted?\nAsked: Sorted list?\n"
        "Announced: The list in Q1 is sorted.\n</clarification>\n\n"
        '<clarification question="Q2" seats="1">\n'
        "Asked: Can we use a calculator?\nNot answered\n</clarification>\n\n"
        '<clarification question="Q3b" seats="2">\n'
        "Asked: Is the answer big-O or an exact count?\nAsked: Do we give the exact count?\n"
        "Answer: Big-O.\nEarlier answer: An exact count.\n</clarification>\n\n"
        '<announcement question="">\nTen minutes left.\n</announcement>'
    )


@pytest.mark.django_db(transaction=True)
def test_regenerating_replaces_the_analysis(examiner: Client, exam: Exam, ai: FakeService) -> None:
    report(exam)
    ai.content = json.dumps(FINDINGS)
    analyse(examiner, exam)
    generated_at = Analysis.objects.get().generated_at
    ai.content = json.dumps(FINDINGS | {"confusions": []})
    page = analyse(examiner, exam)
    assert "Big-O or an exact count" not in page
    assert page.count("None found") == 2
    assert Analysis.objects.get().generated_at > generated_at


@pytest.mark.parametrize(
    ("status", "content"),
    [(400, ""), (200, "Students asked about Q3b."), (200, '{"confusions": []}')],
)
@pytest.mark.django_db(transaction=True)
def test_failed_analysis_says_so(
    examiner: Client, exam: Exam, ai: FakeService, status: int, content: str
) -> None:
    report(exam)
    ai.status, ai.content = status, content
    page = analyse(examiner, exam)
    assert "Couldn&#x27;t analyse the clarifications." in page
    assert "No analysis yet" in page
    assert not Analysis.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_analysis_is_saved_after_the_examiner_leaves(
    examiner: Client, exam: Exam, ai: FakeService
) -> None:
    report(exam)
    ai.content, ai.delay = json.dumps(FINDINGS), 0.5

    async def saved_after_leaving() -> bool:
        client = AsyncClient()
        client.cookies = examiner.cookies
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(client.post(url("examiner:analyse", exam)), 0.1)
        try:
            for _ in range(50):
                if await Analysis.objects.aexists():
                    return True
                await asyncio.sleep(0.1)
            return False
        finally:
            await sync_to_async(connections.close_all)()

    with ThreadPoolExecutor(1) as executor:  # Playwright's tests leave a loop running in this one
        assert executor.submit(asyncio.run, saved_after_leaving()).result()


@pytest.mark.django_db(transaction=True)
def test_analysis_has_its_own_timeout(
    examiner: Client, exam: Exam, ai: FakeService, settings: Settings
) -> None:
    settings.EXAMDESK_AI_ANALYSIS_TIMEOUT = timedelta(seconds=0.2)
    report(exam)
    ai.content, ai.delay = json.dumps(FINDINGS), 0.5
    assert "The AI service took too long." in analyse(examiner, exam)
    assert len(ai.requests) == 1  # not retried


def test_questions_most_asked_first(examiner: Client, exam: Exam) -> None:
    connect(exam)
    three_clarifications(exam)
    q1 = exam.clarifications.get(label="Q1")
    for seat in ("17", "18"):
        report(exam, seat=seat, clarification=q1)
    page = examiner.get(url("examiner:analysis", exam)).content.decode()
    rows = re.findall(
        r'label-chip">(Q\w+)</span></td>\s*<td class="num text-right">(\d+)</td>'
        r'\s*<td class="num text-right">(\d+)</td>',
        page,
    )
    assert rows == [("Q1", "1", "3"), ("Q3b", "2", "2")]


def test_analysis_needs_clarifications(examiner: Client, exam: Exam) -> None:
    connect(exam)
    page = examiner.get(url("examiner:analysis", exam)).content.decode()
    assert f'href="{url("examiner:analysis", exam)}" aria-current="page"' in page
    assert "No clarifications yet" in page
    assert url("examiner:analyse", exam) not in page


def test_no_analysis_without_a_service(examiner: Client, exam: Exam) -> None:
    report(exam)
    page = examiner.get(url("examiner:clarifications", exam), follow=True).content.decode()
    assert url("examiner:analysis", exam) not in page
    assert examiner.get(url("examiner:analysis", exam)).status_code == 404
    assert examiner.post(url("examiner:analyse", exam)).status_code == 404


def test_invigilators_cannot_analyse(invigilator: Client, exam: Exam) -> None:
    connect(exam)
    report(exam)
    assert invigilator.get(url("examiner:analysis", exam)).status_code == 403
    assert invigilator.post(url("examiner:analyse", exam)).status_code == 403
