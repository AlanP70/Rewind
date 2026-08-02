import logging

from sqlalchemy import text

from app.core.db import engine
from app.core.redis import redis

# Connectivity probes for the things core/ owns, not business logic — which is why
# they live here rather than in services/ or repositories/. Both routes in
# api/health.py call check_dependencies and do nothing but map it to a status code.

# uvicorn attaches handlers only to its own loggers, so a getLogger(__name__)
# logger would fall through to logging's last-resort stderr handler and print
# unformatted, untimestamped lines. Borrowing uvicorn's logger puts these in the
# same stream and the same format as every other line in the platform's log tab.
logger = logging.getLogger("uvicorn.error")


async def check_dependencies() -> dict[str, str]:
    return {"db": await _check_db(), "redis": await _check_redis()}


async def _check_db() -> str:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        # The route turns this into one word, "down". Without the traceback a
        # failing deploy is undiagnosable, because the platform withholds traffic
        # from the instance until the healthcheck passes — the log is the only
        # channel out.
        logger.exception("healthcheck: db probe failed")
        return "down"


async def _check_redis() -> str:
    try:
        await redis.ping()
        return "ok"
    except Exception:
        logger.exception("healthcheck: redis probe failed")
        return "down"
