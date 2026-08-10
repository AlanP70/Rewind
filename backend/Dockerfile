# For Render, not for local dev — locally the backend runs on the host and
# compose provides Postgres and Redis only.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY start.sh ./

ENV PATH="/app/.venv/bin:$PATH"

# One image, one service, two processes inside it. start.sh migrates and then runs
# uvicorn and the arq worker together under a trap — see the comments in it for
# why it supervises rather than execs, and why the Docker Command field must stay
# blank. The platform supplies PORT.
CMD ["/app/start.sh"]
