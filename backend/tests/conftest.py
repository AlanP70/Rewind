"""Database fixtures.

Tests run against a real Postgres -- a separate `rewind_test` database, rebuilt
from the migrations at the start of a session and truncated between tests. Not
`Base.metadata.create_all`: the constraints these tests lean on live in the
migration files, and a suite that builds its schema from the models cannot
notice when the two drift apart.

The migrations run in a subprocess with `ALEMBIC_DATABASE_URL` overridden,
because `alembic/env.py` reads that setting itself and would otherwise point the
test run at the development database.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.paths import BACKEND_DIR

TEST_DATABASE_NAME = "rewind_test"

# Everything below `users`. The seed user is inserted by migration 0002 and is
# left in place, so tests get the same hardcoded owner the application uses.
TRUNCATE_TABLES = "chunks, processing_runs, documents, courses"


def _test_url(url_string: str) -> str:
    """The same URL, pointed at the test database.

    `render_as_string(hide_password=False)` rather than `str()`: SQLAlchemy's
    `__str__` masks the password as `***`, which produces a URL that looks
    correct in a traceback and fails authentication.
    """
    return make_url(url_string).set(database=TEST_DATABASE_NAME).render_as_string(
        hide_password=False
    )


@pytest.fixture(scope="session")
def test_database() -> Iterator[str]:
    """Drop, recreate and migrate `rewind_test`. Returns its async URL."""
    # `drivername` is reset to bare `postgresql`: psycopg is being handed this
    # string directly, and libpq does not understand SQLAlchemy's `+psycopg`.
    admin_url = (
        make_url(settings.alembic_database_url)
        .set(database="postgres")
        .set(drivername="postgresql")
    )

    # psycopg is already a dependency, for Alembic. CREATE DATABASE cannot run
    # inside a transaction, hence autocommit.
    import psycopg

    with psycopg.connect(admin_url.render_as_string(hide_password=False), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE_NAME}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DATABASE_NAME}"')

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "ALEMBIC_DATABASE_URL": _test_url(settings.alembic_database_url)},
        check=True,
        capture_output=True,
    )

    yield _test_url(settings.database_url)


@pytest_asyncio.fixture
async def session(test_database: str) -> AsyncIterator[AsyncSession]:
    """A clean session per test.

    The engine is per-test rather than shared: the services under test commit
    their own transactions, so isolation has to come from truncating afterwards
    rather than from rolling back.
    """
    engine = create_async_engine(test_database)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        yield db

    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TRUNCATE_TABLES} CASCADE"))
    await engine.dispose()
