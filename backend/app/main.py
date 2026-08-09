from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import courses, documents, health
from app.core.config import settings
from app.core.db import engine
from app.core.queue import create_queue
from app.core.redis import redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One arq pool for the process, opened here because `create_pool` is async and
    # cannot be a module-level constant the way the plain Redis client is.
    app.state.queue = await create_queue()
    yield
    await app.state.queue.aclose()
    await engine.dispose()
    await redis.aclose()


app = FastAPI(title="Rewind", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(courses.router)
