# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[dev]"

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

RUN groupadd --gid 10001 aethergate \
    && useradd --uid 10001 --gid aethergate --create-home aethergate

# Source, tests, packaging config, and the v2 deploy files are copied for the
# Alembic migrations and the containerized test workflow (`scripts/dev/v2 test`).
# Only the two deploy files are copied here so `deploy/v2/.env` (generated
# secrets) is never baked into the image.
COPY --chown=aethergate:aethergate pyproject.toml ./
COPY --chown=aethergate:aethergate src ./src
COPY --chown=aethergate:aethergate tests ./tests
COPY --chown=aethergate:aethergate deploy/v2/compose.yaml deploy/v2/Dockerfile ./deploy/v2/

RUN chown aethergate:aethergate /app

USER aethergate

EXPOSE 8000

CMD ["uvicorn", "aethergate.main:app", "--host", "0.0.0.0", "--port", "8000"]
