"""The exam's AI service, an OpenAI-compatible API."""

import asyncio
import base64
import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence

from django.conf import settings
from openai import AsyncOpenAI, OpenAI
from openai.types.chat import (
    ChatCompletionContentPartParam,
    ChatCompletionMessageParam,
    ChatCompletionTokenLogprob,
)

from . import pdf
from .models import Clarification, Exam, ExamFile

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

A reply must never say or suggest whether an answer is right, which option to pick, how to \
work anything out, or anything else the paper doesn't say. This holds for yes/no questions \
too. The files may contain answers, solutions, marking schemes or statistics. Never use them.

Examples:
- "Is my answer to 3 correct?" gets "No comment."
- "For 2b, do I give the answer in metres?", when 2b says "Give your answer in metres.", \
gets "2b says: Give your answer in metres."
- "Is it 2(n-1) or 2n-1 in 5a?", when 5a has \\(2n-1\\), gets "5a says: 2n - 1."

Reply with only the words for the student, with maths in plain text rather than LaTeX."""
CONFIRM_DUPLICATES = """\
During an exam, students ask clarifications about questions on the paper. The question number \
is in brackets; parts such as a and b are different questions. Clarifications about the same \
question often ask different things. For each other clarification, write one line: its id, a \
colon, then Yes if it asks the same thing about the same question as the clarification, so that \
one answer serves both, or No. Write nothing else."""


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
    system = "\n\n".join(
        [DRAFT.format(canned=canned), *(f"# {file.name}\n\n{file.text}" for file in files)]
    )
    asked = "\n".join(_distinct_texts(clarification))
    question = f"Exam question: {clarification.label}\nThe student asks:\n{asked}"
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    text = await _reply(exam, exam.ai_text_model, messages)
    return text[: settings.EXAMDESK_TEXT_MAX]


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
