# Exam Desk

Exam Desk relays students' questions during in-person exams. Invigilators report questions from
their phones, examiners answer them, and invigilators take the answers back to the seats.

## How an exam runs

An account holder creates an exam and shares its two join links, one for examiners and one for
invigilators. Joining takes only a name.

Invigilators have four tabs:

- **Report**: a seat, a question number such as `3b` or `General`, and the student's question as
  text or a photo. The seat joins a similar clarification or starts a new one.
- **Open**: unanswered clarifications in their venue.
- **Deliver**: seats with answers to show, marked delivered one by one. An opened seat shows
  other invigilators who has it.
- **Announcements**: messages to read out in their venue.

Settings can list the exam's venues, as suggestions or as the only ones allowed. With only one
venue listed, invigilators start in it. Locking it keeps them there. Seat numbers can be listed
per venue, e.g. `1-200` or `A1-A30`, as suggestions or as the only ones allowed.

Examiners answer clarifications, merge duplicates and move seats between them. Announcing a
clarification sends its answer to every venue to read out. A corrected answer goes back to the
seats that had the old one. Settings can list the exam's question numbers, as suggestions or as
the only ones allowed. Closing an exam makes it read-only and stops the invigilator link.
Settings exports the clarifications to CSV.

## AI service

A sentence-embedding model on the server finds possible duplicates. An exam can also connect an
OpenAI-compatible API in its settings, with three models:

- The **vision model** transcribes photos of questions and reads uploaded PDFs.
- The **fast model** confirms possible duplicates, with a percentage from its logprobs.
- The **careful model** drafts answers from the uploaded exam paper and analyses the clarifications.

A feature works once its model is chosen.

API keys are encrypted with `DJANGO_SECRET_KEY`. After changing it, examiners connect again.

## Deployment

`ghcr.io/examdesk/examdesk` is built for amd64 and arm64: `latest` from `main` and a version tag
for each release. It runs migrations on start and listens on port 8000. It needs PostgreSQL and
a volume at `/data`.

```sh
DJANGO_SECRET_KEY=<50+ random characters>
DJANGO_ALLOWED_HOSTS=examdesk.example.com
DATABASE_URL=postgres://user:password@host:5432/examdesk
DJANGO_EMAIL_HOST=smtp.example.com
DJANGO_EMAIL_USER=examdesk@example.com
DJANGO_EMAIL_PASSWORD=<SMTP password>
DJANGO_DEFAULT_FROM_EMAIL=Exam Desk <examdesk@example.com>
```

Sign-in is by emailed code. Create the first account with `python manage.py createsuperuser`.

Without Docker:

```sh
uv sync --no-dev --extra server
uv run manage.py migrate
uv run manage.py createcachetable
uv run manage.py download_model
uv run manage.py tailwind build
uv run manage.py collectstatic --no-input
DATABASE_URL=sqlite://:memory: uv run manage.py build_demos
uv run gunicorn examdesk.asgi --worker-class uvicorn_worker.UvicornWorker --bind 127.0.0.1:8000 --workers 3
```

The proxy in front should allow 12 MB request bodies and not serve the media folder.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | development key | Required in production |
| `DJANGO_DEBUG` | off | `1` for development |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names |
| `DATABASE_URL` | `postgres://examdesk:examdesk@localhost:5435/examdesk` | PostgreSQL |
| `DJANGO_EMAIL_HOST` | unset | SMTP server for sign-in codes; unset prints them to the console |
| `DJANGO_EMAIL_PORT` | `587` | `587` uses STARTTLS, `465` TLS |
| `DJANGO_EMAIL_USER`, `DJANGO_EMAIL_PASSWORD` | empty | SMTP credentials |
| `DJANGO_DEFAULT_FROM_EMAIL` | `Exam Desk <examdesk@localhost>` | Sender of sign-in codes |
| `DJANGO_TIME_ZONE` | `UTC` | Time zone of new exams when the browser's is unknown, such as `Europe/London` |
| `EXAMDESK_REGISTRATION` | on | `0` allows only accounts added in the admin |
| `EXAMDESK_REGISTRATION_DOMAINS` | empty | Comma-separated email domains that may sign up; empty allows any |
| `EXAMDESK_PROXY_COUNT` | `1`, or `0` with `DJANGO_DEBUG` | Proxies in front, for sign-in rate limits |
| `EXAMDESK_MEDIA_ROOT` | `media/` | Photos and files |
| `EXAMDESK_STATIC_ROOT` | `static/` | Target of `collectstatic` |
| `HF_HOME` | `~/.cache/huggingface` | Where `download_model` puts the model |
| `WEB_CONCURRENCY` | `3` | gunicorn workers in the image |
| `EXAMDESK_LATE_AFTER`, `EXAMDESK_VERY_LATE_AFTER` | `300`, `600` | Seconds before a clarification is late, very late |
| `EXAMDESK_CANNED_ANSWERS` | `No comment.\|Read the question carefully.\|State your assumptions.` | One-click answers, `\|`-separated |
| `EXAMDESK_SIMILARITY_THRESHOLD` | `0.5` | Similarity (0–1) at which questions match |
| `EXAMDESK_TEXT_MAX` | `2000` | Longest question, answer or announcement, in characters |
| `EXAMDESK_PAGE_SIZE` | `24` | Items per page |
| `EXAMDESK_SEAT_CLAIM_FOR` | `300` | Longest seat claim, in seconds |
| `EXAMDESK_SNOOZE_FOR` | `300` | Seconds a put-off announcement alert stays hidden |
| `EXAMDESK_ONLINE_FOR` | `60` | Seconds a member counts as online after last seen |
| `EXAMDESK_SEEN_EVERY` | `30` | Seconds between presence updates |
| `EXAMDESK_HEARTBEAT_EVERY` | `5` | Seconds between heartbeats on a page's stream |
| `EXAMDESK_OFFLINE_AFTER` | `12` | Seconds without a heartbeat before a page shows it is offline |
| `EXAMDESK_AI_TIMEOUT` | `60` | Seconds to wait for an exam's AI service |
| `EXAMDESK_AI_ANALYSIS_TIMEOUT` | `300` | Seconds to wait for an analysis of the clarifications |
| `EXAMDESK_PHOTO_MAX_SIDE` | `800` | Longest photo side after resizing on the phone, in pixels |
| `EXAMDESK_PHOTO_MAX_BYTES` | `30000` | Target photo size after re-encoding on the phone |
| `EXAMDESK_PHOTO_UPLOAD_MAX_BYTES` | `10000000` | Largest photo the server accepts |
| `EXAMDESK_FILE_UPLOAD_MAX_BYTES` | `10000000` | Largest file examiners can upload |
| `EXAMDESK_PHOTO_CACHE_FOR` | `86400` | Seconds browsers cache a photo or PDF page |
| `EXAMDESK_MODEL_REPO` | `Xenova/all-MiniLM-L6-v2` | Hugging Face repository of the similarity model |
| `EXAMDESK_MODEL_REVISION` | pinned commit | Its revision |
| `EXAMDESK_MODEL_FILES` | `onnx/model_quantized.onnx,tokenizer.json` | Its ONNX model and tokenizer |
| `EXAMDESK_MODEL_MAX_TOKENS` | `256` | Its input limit, in tokens |

## Development

Django, [htmx 4](https://four.htmx.org/), [django-cotton](https://django-cotton.com/) and
[daisyUI](https://daisyui.com/) via [django-tailwind-cli](https://django-tailwind-cli.readthedocs.io/).
Each action in `examdesk/services.py` bumps the exam's version, and open pages swap in their
`<c-live>` parts over server-sent events.

Requires Python 3.14, [uv](https://docs.astral.sh/uv/) and PostgreSQL:

```sh
docker run -d --name examdesk-postgres --restart unless-stopped -p 127.0.0.1:5435:5432 \
  -e POSTGRES_USER=examdesk -e POSTGRES_PASSWORD=examdesk -e POSTGRES_DB=examdesk postgres:18-alpine
uv sync
uv run manage.py migrate
uv run manage.py createcachetable
uv run manage.py download_model
DATABASE_URL=sqlite://:memory: uv run manage.py build_demos   # the landing page's demos
uv run manage.py createsuperuser
uv run manage.py tailwind watch   # in its own terminal
DJANGO_DEBUG=1 uv run uvicorn examdesk.asgi:application --reload --timeout-graceful-shutdown 1
```

Sign-in codes are printed in the uvicorn console. `runserver` can't serve event streams.

Quality gate:

```sh
uv run ruff format && uv run ruff check --fix && uv run mypy . && uv run pytest
uv run manage.py check && uv run manage.py makemigrations --check --dry-run
```

The end-to-end test needs `uv run playwright install chromium`.

## License

[MIT](LICENSE). The vendored htmx, Tom Select and Invoker Commands polyfill keep their own
licenses: [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES).
