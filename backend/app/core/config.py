from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved from the package location, not the working directory, so the app reads
# the same .env no matter where uvicorn was started from.
from app.core.paths import BACKEND_DIR


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # Async driver for the app. Sync driver for Alembic. Two variables rather than
    # one, because in production they may point at different hosts (session pooler
    # vs direct connection).
    database_url: str
    alembic_database_url: str

    redis_url: str

    # Optional so the app, the health routes and `ingest --dry-run` all run
    # without a key. Only the embedding step requires it, and it says so when it
    # is missing rather than failing inside the OpenAI client.
    openai_api_key: str | None = None

    # Document bytes. `local` is the default so a fresh clone can ingest and
    # verify with no credentials at all -- the bar Phase 0 set. The deploy sets
    # `supabase`, because the upload endpoint and the worker are separate
    # services with no shared disk.
    storage_backend: Literal["local", "supabase"] = "local"
    storage_local_root: str = ".storage"
    storage_bucket: str = "documents"

    # Optional for the same reason `openai_api_key` is: only the supabase backend
    # reads them, and `get_storage()` names whichever is missing rather than
    # failing inside httpx as a 401.
    supabase_url: str | None = None
    supabase_service_key: str | None = None

    @property
    def storage_root(self) -> Path:
        """Where `LocalStorage` writes.

        A relative value resolves against `backend/`, not the working directory,
        so `python -m app.cli` finds the same files from anywhere. An absolute
        value is taken as-is -- that is what `/` does with one.
        """
        return BACKEND_DIR / self.storage_local_root

    # Comma-separated. A localhost placeholder holds until the first Vercel deploy
    # supplies the real origin.
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
