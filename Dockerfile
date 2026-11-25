# syntax=docker/dockerfile:1

# --- Build stage: install the dependencies into a virtual environment -------------------------
FROM python:3.14-slim AS build

# uv installs exactly what uv.lock says. The version matches the one used in development.
COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Only the two files that say what to install. This layer is rebuilt when a dependency
# changes, not every time the code does.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


# --- Final stage: the application, without build tools, tests or development packages ---------
FROM python:3.14-slim

# The service does not need root.
RUN useradd --create-home --uid 1000 app \
    && mkdir /data \
    && chown app:app /data

COPY --from=build /app/.venv /app/.venv
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
COPY app /app/app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

USER app

# The server runs from /data, so the default database, ./extractions.db, is created
# there. Mount a volume on /data to keep the stored extractions when the container goes.
WORKDIR /data
VOLUME /data

EXPOSE 8000

# The migrations create or update the schema, then the server takes over the process,
# so it receives the stop signal itself and shuts down cleanly.
CMD ["sh", "-c", "alembic -c /app/alembic.ini upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
