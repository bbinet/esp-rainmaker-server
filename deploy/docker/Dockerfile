ARG PYTHON_VERSION=3.11

# Pin to Debian Trixie (Debian 13) for a current, predictable libc /
# OpenSSL / CA-certificates baseline. `python:3.11-slim-trixie` is the
# official Python image variant on Trixie.
FROM python:${PYTHON_VERSION}-slim-trixie AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip wheel --wheel-dir /wheels --no-deps .  \
 && pip download --dest /wheels .


FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1000 rainmaker \
 && useradd --uid 1000 --gid rainmaker --shell /sbin/nologin --create-home rainmaker

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels esp-rainmaker-server \
 && rm -rf /wheels

COPY app /app/app
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

USER rainmaker

EXPOSE 8000 8001

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

# Override via k8s `command:` or docker-compose `command:`:
#   api            → app.entrypoints.api
#   vmq-authz      → app.entrypoints.vmq_authz
#   mqtt-ingestor  → app.entrypoints.mqtt_ingestor
#   worker         → app.entrypoints.worker
CMD ["python", "-m", "app.entrypoints.api"]
