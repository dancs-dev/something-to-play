FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=0

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY app/ app/
COPY config/ config/
COPY manage.py ./
RUN .venv/bin/python manage.py collectstatic --noinput \
    && useradd --system --uid 10001 app \
    && mkdir /data \
    && chown app:app /data

ENV PATH="/app/.venv/bin:$PATH" \
    DJANGO_DB_PATH=/data/db.sqlite3 \
    PYTHONDONTWRITEBYTECODE=1
USER app
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 600 --access-logfile -"]
