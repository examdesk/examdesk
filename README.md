# Exam Desk

Exam Desk relays students' questions during in-person exams. Invigilators enter them on their
phones, examiners answer, and invigilators pass the answers back to the students who asked.
Examiners can also post announcements to all venues.

Django, [htmx 4](https://four.htmx.org/), [daisyUI](https://daisyui.com/) via
[django-tailwind-cli](https://django-tailwind-cli.readthedocs.io/) (no Node),
[django-cotton](https://django-cotton.com/), [Lucide](https://lucide.dev/) and
[Tom Select](https://tom-select.js.org/). A sentence-embedding model on the server flags
duplicate questions. An exam can add its own AI service to transcribe photos of students'
questions, draft answers from the exam paper and confirm duplicates. File pages render Markdown
with [markdown-it-py](https://markdown-it-py.readthedocs.io/) and maths as MathML with
[latex2mathml](https://github.com/roniemartinez/latex2mathml).

## Layout

| Path | Contents |
|---|---|
| `examdesk/models.py` | Exam, ExamFile, FilePage, Clarification, Answer, Report, Delivery, SeatClaim, Announcement, Readout, Member |
| `examdesk/services.py` | One function per action |
| `examdesk/labels.py` | Normalizing question labels, venues and names; natural sort |
| `examdesk/similarity.py` | Sentence-embedding similarity |
| `examdesk/matching.py` | Finding clarifications that ask the same thing |
| `examdesk/ai.py` | Transcribing photos, drafting answers and confirming duplicates with the exam's AI service |
| `examdesk/pdf.py` | Counting and rendering PDF pages with PDFium |
| `examdesk/templatetags/` | Template filters, including Markdown with MathML maths |
| `examdesk/fields.py` | Encrypted text field, for AI service keys |
| `examdesk/forms.py` | Input parsing |
| `examdesk/accounts.py` | Sign-up rules for django-allauth's emailed-code sign-in |
| `examdesk/session.py` | Per-device state: member, filters, snoozed alerts |
| `examdesk/access.py` | The `exam_view` access decorator |
| `examdesk/listener.py` | Listening for new exam versions and passing them to open subscriptions |
| `examdesk/views/` | `exams`, `invigilator`, `examiner`, `live` (streams and refreshes), `common` |
| `examdesk/templates/examdesk/` | Pages; each page's live part is its `live` partial |
| `examdesk/templates/account/` | django-allauth pages and email |
| `examdesk/templates/cotton/` | django-cotton components |
| `examdesk/static/examdesk/` | `app.js`, vendored htmx, Tom Select and Invoker Commands polyfill |
| `tests/` | Unit and view tests, and a Playwright end-to-end test |

## Live updates

Each page subscribes to its exam's version with server-sent events. A database trigger sends a
NOTIFY for each new version, and each server process listens on one PostgreSQL connection. On a new
version a page fetches its live parts in one request: its `live` partial, and the tab bar or nav
badges. Wait times count up in the browser. While nothing changes the server only sends heartbeats.

## Development

Requires Python 3.14, [uv](https://docs.astral.sh/uv/) and PostgreSQL:

```sh
docker run -d --name examdesk-postgres --restart unless-stopped -p 127.0.0.1:5435:5432 \
  -e POSTGRES_USER=examdesk -e POSTGRES_PASSWORD=examdesk -e POSTGRES_DB=examdesk postgres:18-alpine
```

```sh
uv sync
uv run manage.py migrate
uv run manage.py createcachetable
uv run manage.py download_model   # 23 MB
uv run manage.py createsuperuser
uv run manage.py tailwind watch   # in its own terminal: rebuilds the CSS on template changes
DJANGO_DEBUG=1 uv run uvicorn examdesk.asgi:application --reload --timeout-graceful-shutdown 1
```

Give the superuser an email address and sign in with it. There are no passwords: Exam Desk
emails a code, which in development is printed in the uvicorn console. `runserver` can't serve
the pages' streams.

Quality gate:

```sh
uv run ruff format && uv run ruff check --fix && uv run mypy . && uv run pytest
uv run manage.py check && uv run manage.py makemigrations --check --dry-run
```

The Playwright test needs a browser: `uv run playwright install chromium`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | development key | Required in production |
| `DJANGO_DEBUG` | off | `1` for development |
| `DJANGO_EMAIL_HOST` | unset | SMTP server for sign-in codes; unset prints them to the console |
| `DJANGO_EMAIL_PORT` | `587` | `587` uses STARTTLS, `465` TLS |
| `DJANGO_EMAIL_USER`, `DJANGO_EMAIL_PASSWORD` | empty | SMTP credentials |
| `DJANGO_DEFAULT_FROM_EMAIL` | `Exam Desk <examdesk@localhost>` | Sender of sign-in codes |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names |
| `DATABASE_URL` | `postgres://examdesk:examdesk@localhost:5435/examdesk` | PostgreSQL |
| `EXAMDESK_MEDIA_ROOT` | `media/` | Photos and PDFs, under `exams/<exam id>/` |
| `EXAMDESK_STATIC_ROOT` | `static/` | Target of `collectstatic` |
| `HF_HOME` | `~/.cache/huggingface` | Where `download_model` puts the model |
| `EXAMDESK_HEARTBEAT_EVERY` | `5` | Seconds between heartbeats on a page's stream |
| `EXAMDESK_OFFLINE_AFTER` | `12` | Seconds without a heartbeat before a page says it is offline and reconnects |
| `EXAMDESK_SEEN_EVERY` | `30` | Seconds between presence updates |
| `EXAMDESK_ONLINE_FOR` | `60` | Seconds a member counts as online after last seen |
| `EXAMDESK_SEAT_CLAIM_FOR` | `300` | Longest seat claim, in seconds |
| `EXAMDESK_SNOOZE_FOR` | `300` | Seconds a snoozed announcement alert stays hidden |
| `EXAMDESK_LATE_AFTER` | `300` | Seconds before a clarification is late |
| `EXAMDESK_VERY_LATE_AFTER` | `600` | Seconds before it is very late |
| `EXAMDESK_CANNED_ANSWERS` | `No comment.\|Read the question carefully.\|State your assumptions.` | One-click answers, `\|`-separated |
| `EXAMDESK_SIMILARITY_THRESHOLD` | `0.5` | Similarity (0–1) at which questions match |
| `EXAMDESK_MODEL_REPO` | `Xenova/all-MiniLM-L6-v2` | Hugging Face model repository |
| `EXAMDESK_MODEL_REVISION` | pinned commit | Model revision |
| `EXAMDESK_MODEL_FILES` | `onnx/model_quantized.onnx,tokenizer.json` | ONNX model and tokenizer files |
| `EXAMDESK_MODEL_MAX_TOKENS` | `256` | Model input limit, in tokens |
| `EXAMDESK_AI_TIMEOUT` | `60` | Seconds to wait for an exam's AI service |
| `EXAMDESK_PHOTO_MAX_SIDE` | `1600` | Longest photo side after phone resizing, in pixels |
| `EXAMDESK_PHOTO_MAX_BYTES` | `200000` | Target photo size after phone re-encoding |
| `EXAMDESK_PHOTO_UPLOAD_MAX_BYTES` | `10000000` | Largest photo the server accepts |
| `EXAMDESK_FILE_UPLOAD_MAX_BYTES` | `10000000` | Largest file examiners can upload for the AI |
| `EXAMDESK_PHOTO_CACHE_FOR` | `86400` | Seconds browsers cache a photo or PDF page |
| `EXAMDESK_REGISTRATION` | on | `0` stops people signing up; only accounts added in the admin can sign in |
| `EXAMDESK_REGISTRATION_DOMAINS` | empty | Comma-separated email domains that may sign up; empty allows any |
| `EXAMDESK_PROXY_COUNT` | `1`, or `0` with `DJANGO_DEBUG` | Proxies in front that append to `X-Forwarded-For`; sign-in rate limits go by the client address before them |
| `EXAMDESK_BACKUP_DAYS` | `14` | Backup retention shown when deleting an exam |
| `EXAMDESK_TEXT_MAX` | `2000` | Longest question, answer or announcement, in characters |
| `EXAMDESK_PAGE_SIZE` | `24` | Exams, clarifications, announcements or seats per page |
| `WEB_CONCURRENCY` | `3` | gunicorn workers in the Docker image |

With `DJANGO_DEBUG` off, cookies are secure-only and `X-Forwarded-Proto` is trusted.

## AI service

Exam Desk has no AI service of its own. Examiners connect one in an exam's settings: any
OpenAI-compatible API over HTTPS. Connecting lists the service's models; Disconnect forgets the
address, key and models.

Examiners then pick two models. The vision model transcribes photos taken while reporting and
reads PDF pages; a transcription fills in the question for the invigilator to correct. The text
model drafts answers and confirms possible duplicates. A model without reasoning answers in
about 2 seconds; a reasoning model can take 20.

Examiners can also upload the paper and its errata as PDF or text files. The vision model reads
each PDF page as an image, which keeps formulas and scanned pages. Pages are read four at a time
while the exam's settings are open; any left unread are read the next time they open. Opening a
file shows each page beside its text.

Draft with AI sends the files' text and the student's question to the text model. Its reply
replaces the answer for the examiner to check and send. The AI is told to quote the paper or reply
with a canned answer, and never to use answers in the files.

With an AI service, the text model checks the ten clarifications the sentence-embedding model
finds most alike, in one request. The page lists those it finds ask the same thing, with its
confidence from the reply's logprobs. The reply is cached until those ten change. If the service
fails, the page lists the most alike.

API keys are encrypted with `DJANGO_SECRET_KEY`. After changing it, examiners disconnect and
connect again.

## Deployment

### Docker image

Each push to `main` publishes `ghcr.io/examdesk/examdesk:latest` (and `:<version>` for `v*`
tags) for amd64 and arm64. The image has the stylesheet, static files and model built in,
serves static files itself, runs migrations on start and listens on port 8000. Keep `/data`
on a volume for photos and PDFs.

On [Dokploy](https://dokploy.com/):

1. Create a PostgreSQL database and copy its internal connection URL.
2. Create an application with the Docker provider and image `ghcr.io/examdesk/examdesk:latest`.
3. Set the environment:
   ```sh
   DJANGO_SECRET_KEY=<50+ random characters>
   DJANGO_ALLOWED_HOSTS=examdesk.example.com
   DATABASE_URL=postgres://user:password@host:5432/examdesk
   DJANGO_EMAIL_HOST=smtp.example.com
   DJANGO_EMAIL_USER=examdesk@example.com
   DJANGO_EMAIL_PASSWORD=<SMTP password>
   DJANGO_DEFAULT_FROM_EMAIL=Exam Desk <examdesk@example.com>
   ```
4. Add a volume mount at `/data`, a domain on port 8000 with HTTPS, and deploy.
5. Create a staff account from the application's terminal:
   `python manage.py createsuperuser`, with your email address.

### Without Docker

gunicorn behind Caddy, with PostgreSQL.

```sh
uv sync --no-dev --extra server
uv run manage.py migrate
uv run manage.py createcachetable
uv run manage.py download_model
uv run manage.py tailwind build
uv run manage.py collectstatic --no-input
uv run gunicorn examdesk.asgi --worker-class uvicorn_worker.UvicornWorker --bind 127.0.0.1:8000 --workers 3
```

Photos and PDFs go through the app, so don't expose the media folder:

```caddy
examdesk.example.com {
    request_body {
        max_size 12MB
    }
    reverse_proxy 127.0.0.1:8000
}
```

### Backups

Back up the database and media folder nightly, keeping as many days as `EXAMDESK_BACKUP_DAYS`:

```sh
pg_dump -Fc examdesk > /backup/examdesk-$(date +%F).dump
rsync -a /srv/examdesk/media/ /backup/media-$(date +%F)/
find /backup -mindepth 1 -maxdepth 1 -mtime +14 -exec rm -rf {} +
```

## License

[MIT](LICENSE). The vendored htmx, Tom Select and Invoker Commands polyfill keep their own
licenses; see [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES).
