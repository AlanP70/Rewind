"""The arq connection pool the API enqueues jobs through.

Separate from `core/redis.py`, which holds a plain client used for the health
check. arq wraps its own protocol around Redis -- job ids, retry counters,
serialised payloads -- and mixing the two would invite something to write a raw
key where arq expects its own.

Created once in the app's lifespan rather than per request: `create_pool` opens a
connection, and doing that on every upload would make the cost of enqueuing
depend on how often anyone uploads. The worker does not use this at all; it gets
its own pool from `WorkerSettings`.
"""

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings


async def create_queue() -> ArqRedis:
    return await create_pool(RedisSettings.from_dsn(settings.redis_url))
