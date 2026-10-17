FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/datepipette/.venv
WORKDIR /workspace

COPY pyproject.toml uv.lock .python-version ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/src backend/src
RUN uv sync --frozen --package datepipette --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime

ENV PATH=/opt/datepipette/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN groupadd --gid 10001 datepipette \
    && useradd --uid 10001 --gid datepipette --no-create-home --shell /usr/sbin/nologin datepipette \
    && install -d -o datepipette -g datepipette /var/lib/datepipette/snapshots

COPY --from=builder /opt/datepipette/.venv /opt/datepipette/.venv
COPY backend/alembic.ini backend/alembic.ini
COPY backend/migrations backend/migrations

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "datepipette.main:app", "--host", "0.0.0.0", "--port", "8000"]
