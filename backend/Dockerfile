FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# libsndfile decodes uploaded speech without invoking a shell or accepting
# arbitrary codecs. ffmpeg is intentionally absent; unsupported media is
# rejected and can be normalized by the separate ingestion pipeline.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# CPU-only torch: the default index ships CUDA wheels that are several GB and
# useless without a GPU.
RUN pip install --no-cache-dir --timeout 180 --retries 20 \
      --extra-index-url https://download.pytorch.org/whl/cpu .

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN useradd --create-home --uid 10001 faceid
# The object store is a mounted volume. Creating it here, owned by the runtime
# user, means Docker seeds an empty named volume with this ownership instead of
# handing the process a root-owned directory it cannot write to.
RUN mkdir -p /srv/objects /srv/model-cache \
    && chown -R faceid:faceid /srv/objects /srv/model-cache
USER faceid

EXPOSE 8000
CMD ["uvicorn", "app.main:get_asgi_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
