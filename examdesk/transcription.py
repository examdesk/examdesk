"""Transcribing a photographed question with the exam's AI service, an OpenAI-compatible API."""

import base64

from django.conf import settings
from openai import AsyncOpenAI

from .models import Exam

PROMPT = (
    "Transcribe the handwriting in this photo exactly, and reply with nothing else. "
    "Leave out names and student numbers."
)


async def transcribe(exam: Exam, photo: bytes, content_type: str) -> str:
    """The handwriting in `photo`. Raises `openai.APIError`."""
    image = f"data:{content_type};base64,{base64.b64encode(photo).decode()}"
    async with AsyncOpenAI(
        base_url=exam.ai_base_url,
        api_key=exam.ai_api_key,
        timeout=settings.EXAMDESK_AI_TIMEOUT.total_seconds(),
    ) as client:
        completion = await client.chat.completions.create(
            model=exam.ai_model,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image}},
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        )
    text = completion.choices[0].message.content or ""
    return text.strip()[: settings.EXAMDESK_TEXT_MAX]
