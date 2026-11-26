"""The exam's AI service, an OpenAI-compatible API."""

import asyncio
import base64
import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence

from django.conf import settings
from openai import AsyncOpenAI, OpenAI
from openai.types.chat import (
    ChatCompletionContentPartParam,
    ChatCompletionMessageParam,
    ChatCompletionTokenLogprob,
)
from pydantic import BaseModel

from . import pdf
from .labels import natural
from .models import Announcement, Clarification, Exam, ExamFile

TRANSCRIBE = (
    "Transcribe the handwriting in this photo exactly, and reply with nothing else. "
    "Leave out names and student numbers."
)
READ_PAGE = (
    "Transcribe this page of an exam paper. Reply with only its text, using Markdown for "
    "structure and LaTeX for maths, and never put the text in a code block. Leave out marks "
    "showing correct answers, filled-in answers and statistics."
)
DRAFT = """\
You draft replies to students' questions during an exam. An examiner checks each reply \
before the student hears it.

Students must answer the exam on their own. When a sentence of the exam paper below, or a \
correction the files list, settles the question, reply by quoting it. Otherwise reply with \
exactly one of these: {canned}

When the student says the paper has a mistake, check their claim against the paper. If the \
paper contradicts itself there, reply with a correction that says what it should read. If it \
doesn't, quote the sentence that shows it.

A reply must never say or suggest whether an answer is right, which option to pick, how to \
work anything out, or anything else the paper doesn't say. This holds for yes/no questions \
too. The files may contain answers, solutions, marking schemes or statistics. Never use them.

Examples:
- "Is my answer to 3 correct?" gets "No comment."
- "For 2b, do I give the answer in metres?", when 2b says "Give your answer in metres.", \
gets "2b says: Give your answer in metres."
- "Is it 2(n-1) or 2n-1 in 5a?", when 5a has \\(2n-1\\), gets "5a says: 2n - 1."
- "4b says 6 cities but the table has 7", when 4b says "the 6 cities in the table" and the \
table lists 7, gets "Correction to 4b: the table has 7 cities, not 6."
- The same question, when the table in 4b lists 6 cities, gets "4b says: the 6 cities in the \
table."

Reply with only the words for the student, with maths in plain text rather than LaTeX."""
CONFIRM_DUPLICATES = """\
During an exam, students ask clarifications about questions on the paper. The question number \
is in brackets; parts such as a and b are different questions. Clarifications about the same \
question often ask different things. For each other clarification, write one line: its id, a \
colon, then Yes if it asks the same thing about the same question as the clarification, so that \
one answer serves both, or No. Write nothing else."""
ANALYSE = """\
During or after an exam, an examiner reviews the clarifications students asked, to find what \
to announce now and what to fix in the paper next time.

Each clarification gives its question number and how many seats asked it, then these lines:
- Asked: what a student asked.
- Answer: the answer sent to those seats.
- Earlier answer: an answer that a correction replaced.
- Announced: an answer read out in every venue.
Announcements outside a clarification were read out in every venue too. The exam paper and other \
files follow, if there are any.

Reply with only a JSON object of this form, and never put it in a code block:
{"confusions": [{"title": "...", "questions": ["3b"], "description": "..."}],
 "unclear_wording": [{"question": "3b", "explanation": "...", "suggested_wording": "..."}],
 "errors": [{"question": "4", "description": "..."}],
 "inconsistent_answers": [{"question": "2a", "description": "..."}]}

- confusions: what students misunderstood again and again, across seats or questions, most \
asked first.
- unclear_wording: sentences of the paper that students read in more than one way. Explain the \
readings, and rewrite the sentence so that it reads only one way.
- errors: mistakes in the paper that the clarifications revealed, each with a correction that \
can be read out as an announcement.
- inconsistent_answers: answers or announcements that disagree with each other or with the paper.

Give question numbers as the clarifications do. Leave a list empty when nothing fits. Write \
plain sentences, with maths in plain text rather than LaTeX."""


class Confusion(BaseModel):
    title: str
    questions: list[str]
    description: str


class UnclearWording(BaseModel):
    question: str
    explanation: str
    suggested_wording: str


class Problem(BaseModel):
    question: str
    description: str


class Findings(BaseModel):
    confusions: list[Confusion]
    unclear_wording: list[UnclearWording]
    errors: list[Problem]
    inconsistent_answers: list[Problem]


def list_models(base_url: str, api_key: str) -> list[str]:
    """Raises `openai.APIError`."""
    timeout = settings.EXAMDESK_AI_TIMEOUT.total_seconds()
    with OpenAI(base_url=base_url, api_key=api_key, timeout=timeout) as client:
        return sorted(model.id for model in client.models.list())


def _client(exam: Exam) -> AsyncOpenAI:
    timeout = settings.EXAMDESK_AI_TIMEOUT.total_seconds()
    return AsyncOpenAI(base_url=exam.ai_base_url, api_key=exam.ai_api_key, timeout=timeout)


async def _reply(exam: Exam, model: str, messages: list[ChatCompletionMessageParam]) -> str:
    """Raises `openai.APIError`."""
    async with _client(exam) as client:
        completion = await client.chat.completions.create(
            model=model, temperature=0, messages=messages
        )
    text = completion.choices[0].message.content or ""
    return text.strip()


async def _read_image(exam: Exam, image: bytes, content_type: str, prompt: str) -> str:
    url = f"data:{content_type};base64,{base64.b64encode(image).decode()}"
    content: list[ChatCompletionContentPartParam] = [
        {"type": "image_url", "image_url": {"url": url}},
        {"type": "text", "text": prompt},
    ]
    return await _reply(exam, exam.ai_vision_model, [{"role": "user", "content": content}])


async def transcribe(exam: Exam, photo: bytes, content_type: str) -> str:
    """The handwriting in `photo`."""
    text = await _read_image(exam, photo, content_type, TRANSCRIBE)
    return text[: settings.EXAMDESK_TEXT_MAX]


async def read_page(exam: Exam, document: bytes, number: int) -> str:
    image = await asyncio.to_thread(pdf.render_page, document, number)
    return await _read_image(exam, image, "image/png", READ_PAGE)


async def draft_answer(exam: Exam, clarification: Clarification, files: Iterable[ExamFile]) -> str:
    """Requires the clarification's reports and the files' pages prefetched."""
    canned = " | ".join(settings.EXAMDESK_CANNED_ANSWERS)
    system = _prompt_with_files(DRAFT.format(canned=canned), files)
    asked = "\n".join(_distinct_texts(clarification))
    question = f"Exam question: {clarification.label}\nThe student asks:\n{asked}"
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    text = await _reply(exam, exam.ai_text_model, messages)
    return text[: settings.EXAMDESK_TEXT_MAX]


def _prompt_with_files(prompt: str, files: Iterable[ExamFile]) -> str:
    return "\n\n".join([prompt, *(f"# {file.name}\n\n{file.text}" for file in files)])


async def analyse(
    exam: Exam,
    clarifications: Iterable[Clarification],
    announcements: Sequence[Announcement],
    files: Iterable[ExamFile],
) -> Findings:
    """Requires the clarifications' reports and answers, and the files' pages, prefetched.

    Raises `openai.APIError`, or `pydantic.ValidationError` for a reply that isn't the JSON asked
    for.
    """
    announcements_by_clarification: defaultdict[int, list[str]] = defaultdict(list)
    for announcement in announcements:
        if announcement.clarification_id:
            announcements_by_clarification[announcement.clarification_id].append(announcement.text)
    elements = [
        _clarification_element(clarification, announcements_by_clarification[clarification.pk])
        for clarification in sorted(clarifications, key=lambda c: natural(c.label))
    ]
    elements += [
        f'<announcement question="{a.label}">\n{a.text}\n</announcement>'
        for a in announcements
        if not a.clarification_id
    ]
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": _prompt_with_files(ANALYSE, files)},
        {"role": "user", "content": "\n\n".join(elements)},
    ]
    timeout = settings.EXAMDESK_AI_ANALYSIS_TIMEOUT.total_seconds()
    # A timeout isn't retried.
    async with _client(exam).with_options(timeout=timeout, max_retries=0) as client:
        completion = await client.chat.completions.create(
            model=exam.ai_text_model, temperature=0, messages=messages
        )
    return Findings.model_validate_json(completion.choices[0].message.content or "")


def _clarification_element(clarification: Clarification, announcement_texts: list[str]) -> str:
    answers = [answer.text for answer in clarification.answers.all()]
    lines = [
        *(f"Asked: {text}" for text in _distinct_texts(clarification)),
        *(f"Answer: {text}" for text in answers[:1]),
        *(f"Earlier answer: {text}" for text in answers[1:]),
        *(f"Announced: {text}" for text in announcement_texts),
    ]
    if not answers and not announcement_texts:
        lines.append("Not answered")
    seat_count = len(clarification.reports.all())
    opening_tag = f'<clarification question="{clarification.label}" seats="{seat_count}">'
    return "\n".join([opening_tag, *lines, "</clarification>"])


def _distinct_texts(clarification: Clarification) -> list[str]:
    return list(dict.fromkeys(text for text in clarification.texts if text))


def _labeled_texts(clarification: Clarification) -> str:
    return f"[{clarification.label}] {' / '.join(_distinct_texts(clarification))}"


def _duplicates_messages(
    clarification: Clarification, candidates: Sequence[Clarification]
) -> list[ChatCompletionMessageParam]:
    tagged = "\n\n".join(
        f'<clarification id="C{n}">\n{_labeled_texts(candidate)}\n</clarification>'
        for n, candidate in enumerate(candidates, 1)
    )
    prompt = (
        f"Clarification:\n<clarification>\n{_labeled_texts(clarification)}\n</clarification>\n\n"
        f"Other clarifications:\n\n{tagged}\n\n"
        f'Reply with {len(candidates)} line(s), such as "C1: Yes".'
    )
    return [
        {"role": "system", "content": CONFIRM_DUPLICATES},
        {"role": "user", "content": prompt},
    ]


def duplicates_cache_key(
    exam: Exam, clarification: Clarification, candidates: Sequence[Clarification]
) -> str:
    """Changes with the service, the model and the prompt."""
    messages = _duplicates_messages(clarification, candidates)
    data = json.dumps([exam.ai_base_url, exam.ai_text_model, messages])
    return "duplicates:" + hashlib.sha256(data.encode()).hexdigest()


async def confirm_duplicates(
    exam: Exam, clarification: Clarification, candidates: Sequence[Clarification]
) -> list[float]:
    """For each of `candidates`, the probability that it asks the same thing as `clarification`:
    1 or 0 without logprobs. Requires the clarifications' reports prefetched.

    Raises `openai.APIError`, or `ValueError` for a reply without a yes or no per candidate.
    """
    async with _client(exam) as client:
        completion = await client.chat.completions.create(
            model=exam.ai_text_model,
            temperature=0,
            messages=_duplicates_messages(clarification, candidates),
            logprobs=True,
            top_logprobs=5,
        )
    choice = completion.choices[0]
    if choice.logprobs and choice.logprobs.content:
        probabilities = [
            _yes_probability(token)
            for token in choice.logprobs.content
            if token.token.strip().lower() in ("yes", "no")
        ]
    else:
        words = re.findall(r"\b(yes|no)\b", (choice.message.content or "").lower())
        probabilities = [float(word == "yes") for word in words]
    if len(probabilities) != len(candidates):
        raise ValueError(f"Expected {len(candidates)} yes or no answers, got {len(probabilities)}")
    return probabilities


def _yes_probability(token: ChatCompletionTokenLogprob) -> float:
    """P(yes) among the token's yes and no alternatives, or the token itself without any."""
    probabilities = {"yes": 0.0, "no": 0.0}
    for alternative in token.top_logprobs:
        if (word := alternative.token.strip().lower()) in probabilities:
            probabilities[word] += math.exp(alternative.logprob)
    if total := probabilities["yes"] + probabilities["no"]:
        return probabilities["yes"] / total
    return float(token.token.strip().lower() == "yes")
