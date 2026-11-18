"""The exam's own AI service: transcribing photos, drafting answers and finding duplicates."""

import json
import math
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from pytest_django.fixtures import Settings

from examdesk import ai as ai_service
from examdesk import services
from examdesk.models import Exam, ExamFile
from tests.helpers import report, url

pytestmark = pytest.mark.django_db

SERVICE = {"base_url": "https://ai.example.edu/v1", "api_key": "sk-1"}
MODELS = ["text", "vision"]


def connect(exam: Exam, base_url: str = SERVICE["base_url"]) -> None:
    services.connect_ai_service(exam, base_url=base_url, api_key="sk-1", models=MODELS)
    services.choose_ai_models(exam, vision_model="vision", text_model="text")


class FakeService(ThreadingHTTPServer):
    """An OpenAI-compatible service on localhost that answers with `status` and `content`."""

    status = 200
    content = " Can we assume x > 0?\n"
    logprobs: list[dict[str, Any]] | None = None
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
        threading.Thread(target=server.serve_forever, daemon=True).start()
        connect(exam, server.base_url)
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


def test_lists_the_services_models(ai: FakeService) -> None:
    assert ai_service.list_models(ai.base_url, "sk-1") == MODELS


def test_examiner_connects_then_picks_models(
    examiner: Client, exam: Exam, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_service, "list_models", lambda base_url, api_key: MODELS)
    response = examiner.post(url("examiner:connect_ai_service", exam), SERVICE)
    assert response["Location"] == url("examiner:settings", exam)
    assert (saved(exam).ai_api_key, saved(exam).ai_models) == ("sk-1", MODELS)
    page = examiner.get(url("examiner:settings", exam)).content.decode()
    assert "Connected" in page
    assert '<option value="vision">vision</option>' in page
    assert "sk-1" not in page
    assert not saved(exam).has_ai_service
    response = examiner.post(url("examiner:choose_ai_models", exam), {"vision_model": "vision"})
    assert response.status_code == 400
    assert not saved(exam).has_ai_service
    models = {"vision_model": "vision", "text_model": "text"}
    examiner.post(url("examiner:choose_ai_models", exam), models)
    assert saved(exam).has_ai_service


def test_only_listed_models_can_be_picked(examiner: Client, exam: Exam) -> None:
    connect(exam)
    examiner.post(
        url("examiner:choose_ai_models", exam), {"vision_model": "gpt", "text_model": "text"}
    )
    assert saved(exam).ai_vision_model == "vision"


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
    assert not saved(exam).has_ai_service


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
    assert body["model"] == "text"
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
    assert "Reading · 2 pages left" in page
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
    image, prompt = body["messages"][0]["content"]
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert "never put the text in a code block" in prompt["text"]
    page = examiner.get(url("examiner:settings", exam)).content.decode()
    assert "Reading · 1 page left" in page
    read(examiner, exam, file, 1)
    page = examiner.get(url("examiner:settings", exam)).content.decode()
    assert "data-read-pages" not in page


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
    assert "Not read yet." in page
    assert url("examiner:page_image", exam, file.pk, 2) in page
    image = examiner.get(url("examiner:page_image", exam, file.pk, 2))
    assert image["Content-Type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")
    assert examiner.get(url("examiner:page_image", exam, file.pk, 3)).status_code == 404
    assert invigilator.get(url("examiner:file", exam, file.pk)).status_code == 403


def test_text_files_show_their_text_without_images(examiner: Client, exam: Exam) -> None:
    connect(exam)
    upload(examiner, exam, "errata.md", b"Q3b: x is a positive integer.")
    [file] = exam.files.all()
    page = examiner.get(url("examiner:file", exam, file.pk)).content.decode()
    assert "Q3b: x is a positive integer." in page
    assert "/image/" not in page


def three_clarifications(exam: Exam) -> None:
    for seat, label, text in [
        ("B14", "Q3b", "Is the answer big-O or an exact count?"),
        ("B15", "Q1", "Is the list sorted?"),
        ("B16", "Q3b", "Do we give the exact count?"),
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
    assert "Finding duplicates" in page
    assert "% alike" not in page
    ai.content = "C1: Yes\nC2: No"
    status, panel = duplicates(examiner, exam)
    assert status == 200
    assert panel.count("% match") == 1
    assert "100% match" in panel  # without logprobs
    assert "big-O" in panel
    [(_, body)] = ai.requests
    assert body["model"] == "text"
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
def test_replies_are_cached(examiner: Client, exam: Exam, ai: FakeService) -> None:
    three_clarifications(exam)
    ai.content = "C1: Yes\nC2: Yes"
    duplicates(examiner, exam)
    assert duplicates(examiner, exam)[1].count("% match") == 2
    assert len(ai.requests) == 1
    report(exam, seat="B17", label="Q1", text="Is the input list sorted?")
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
