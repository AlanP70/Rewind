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
COPY start.sh start-worker.sh ./

ENV PATH="/app/.venv/bin:$PATH"

# One image, two services. The API runs this default; the worker service overrides
# it with /app/start-worker.sh (see render.yaml). Same image because they share the
# same source tree and the same settings — building the worker separately is how
# the two drift into disagreeing about which storage backend the bytes are in.
#
# start.sh runs the migration and then execs uvicorn — see the comment in it for
# why this is a script and not an inline command. The platform supplies PORT.
CMD ["/app/start.sh"]
