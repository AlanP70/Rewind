from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)

# expire_on_commit=False so objects stay readable after commit -- the CLI commits
# and then prints what it wrote.
async_session = async_sessionmaker(engine, expire_on_commit=False)
