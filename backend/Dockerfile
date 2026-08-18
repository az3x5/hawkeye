FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN useradd --create-home --uid 10001 faceid
USER faceid

EXPOSE 8000
CMD ["uvicorn", "app.main:get_asgi_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
