FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/app/hf \
    EXAMDESK_MEDIA_ROOT=/data/media

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --extra server --extra postgres
COPY . .
RUN uv sync --locked --no-dev --extra server --extra postgres \
    && python manage.py tailwind build \
    && python manage.py collectstatic --no-input \
    && python manage.py download_model \
    && rm -rf .django_tailwind_cli

RUN useradd --system --uid 1000 examdesk && mkdir -p /data/media && chown -R examdesk /data
USER examdesk
ENV HF_HUB_OFFLINE=1
VOLUME /data
EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate --no-input && python manage.py createcachetable && exec gunicorn examdesk.wsgi --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-3} --access-logfile -"]
