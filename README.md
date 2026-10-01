# Exam Desk

Exam Desk relays students' questions during in-person exams. Invigilators enter them on their
phones, examiners answer, and invigilators pass the answers back to the students who asked.
Examiners can also post announcements to all venues.

Django, [htmx 4](https://four.htmx.org/), [daisyUI](https://daisyui.com/) via
[django-tailwind-cli](https://django-tailwind-cli.readthedocs.io/) (no Node),
[django-cotton](https://django-cotton.com/), [Lucide](https://lucide.dev/) and
[Tom Select](https://tom-select.js.org/). A sentence-embedding model on the server flags
duplicate questions.

## Layout

| Path | Contents |
|---|---|
| `examdesk/models.py` | Exam, Clarification, Answer, Report, Delivery, SeatClaim, Announcement, Readout, Member |
| `examdesk/services.py` | One function per action |
| `examdesk/labels.py` | Normalizing question labels, venues and names; natural sort |
| `examdesk/similarity.py` | Sentence-embedding similarity |
| `examdesk/matching.py` | Finding clarifications that ask the same thing |
| `examdesk/forms.py` | Input parsing |
| `examdesk/accounts.py` | Sign-up rules for django-allauth's emailed-code sign-in |
| `examdesk/session.py` | Per-device state: member, filters, snoozed alerts |
| `examdesk/access.py` | The `exam_view` access decorator |
| `examdesk/views/` | `exams`, `invigilator`, `examiner`, `live` (polling), `common` |
| `examdesk/templates/examdesk/` | Pages; each page's polled part is its `live` partial |
| `examdesk/templates/account/` | django-allauth pages and email |
| `examdesk/templates/cotton/` | django-cotton components |
| `examdesk/static/examdesk/` | `app.js`, vendored htmx, Tom Select and Invoker Commands polyfill |
| `tests/` | Unit and view tests, and a Playwright end-to-end test |

## Development

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run manage.py migrate
uv run manage.py createcachetable
uv run manage.py download_model   # 23 MB
uv run manage.py createsuperuser
DJANGO_DEBUG=1 uv run manage.py tailwind runserver   # rebuilds the CSS on template changes
```

Give the superuser an email address and sign in with it. There are no passwords: Exam Desk
emails a code, which in development is printed in the `runserver` console.

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
| `DATABASE_URL` | `sqlite:///db.sqlite3` | e.g. `postgres://user:password@host/examdesk` |
| `EXAMDESK_MEDIA_ROOT` | `media/` | Photos, under `exams/<exam id>/` |
| `EXAMDESK_STATIC_ROOT` | `static/` | Target of `collectstatic` |
| `HF_HOME` | `~/.cache/huggingface` | Where `download_model` puts the model |
| `EXAMDESK_POLL_EVERY` | `2` | Seconds between polls of a page's live part |
| `EXAMDESK_OFFLINE_AFTER` | `6` | Seconds without a successful poll before a page says it is offline |
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
| `EXAMDESK_PHOTO_MAX_SIDE` | `1600` | Longest photo side after phone resizing, in pixels |
| `EXAMDESK_PHOTO_MAX_BYTES` | `200000` | Target photo size after phone re-encoding |
| `EXAMDESK_PHOTO_UPLOAD_MAX_BYTES` | `10000000` | Largest photo the server accepts |
| `EXAMDESK_PHOTO_CACHE_FOR` | `86400` | Seconds browsers cache a photo |
| `EXAMDESK_REGISTRATION` | on | `0` stops people signing up; only accounts added in the admin can sign in |
| `EXAMDESK_REGISTRATION_DOMAINS` | empty | Comma-separated email domains that may sign up; empty allows any |
| `EXAMDESK_PROXY_COUNT` | `1`, or `0` with `DJANGO_DEBUG` | Proxies in front that append to `X-Forwarded-For`; sign-in rate limits go by the client address before them |
| `EXAMDESK_BACKUP_DAYS` | `14` | Backup retention shown when deleting an exam |
| `EXAMDESK_TEXT_MAX` | `2000` | Longest question, answer or announcement, in characters |
| `WEB_CONCURRENCY` | `3` | gunicorn workers in the Docker image |

With `DJANGO_DEBUG` off, cookies are secure-only and `X-Forwarded-Proto` is trusted.

## Deployment

### Docker image

Each push to `main` publishes `ghcr.io/examdesk/examdesk:latest` (and `:<version>` for `v*`
tags) for amd64 and arm64. The image has the stylesheet, static files and model built in,
serves static files itself, runs migrations on start and listens on port 8000. It includes
the PostgreSQL driver; keep `/data` on a volume for photos.

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

gunicorn behind Caddy, with PostgreSQL or MySQL.

```sh
uv sync --no-dev --extra server --extra postgres   # or --extra mysql
uv run manage.py migrate
uv run manage.py createcachetable
uv run manage.py download_model
uv run manage.py tailwind build
uv run manage.py collectstatic --no-input
uv run gunicorn examdesk.wsgi --bind 127.0.0.1:8000 --workers 3
```

Photos go through the app, so don't expose the media folder:

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
