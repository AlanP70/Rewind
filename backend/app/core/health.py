from sqlalchemy import text

from app.core.db import engine
from app.core.redis import redis

# Connectivity probes for the things core/ owns, not business logic — which is why
# they live here rather than in services/ or repositories/. Both routes in
# api/health.py call check_dependencies and do nothing but map it to a status code.


async def check_dependencies() -> dict[str, str]:
    return {"db": await _check_db(), "redis": await _check_redis()}


async def _check_db() -> str:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "down"


async def _check_redis() -> str:
    try:
        await redis.ping()
        return "ok"
    except Exception:
        return "down"
