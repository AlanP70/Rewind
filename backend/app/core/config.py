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

    # How long a run may sit at `running` before the status endpoint calls it
    # stale. Crash *detection*, deliberately ours rather than arq's -- arq's own
    # mechanism lives in the Redis that may vanish, and Render's free Key Value
    # plan has no persistence.
    #
    # Must exceed `job_timeout` + 10s, which is arq's in-progress lock -- it holds
    # a hard-killed job's claim for exactly that long, after which any worker
    # re-claims it (`arq/worker.py`, `in_progress_timeout_s` and the `psetex` in
    # `_poll_iteration`). Verified by killing a worker mid-job: re-claimed after
    # 25.18s against a 15s timeout. Below that bound the endpoint would report
    # `stale` on a job arq is about to pick up by itself. 960 > 910 with 50s spare.
    #
    # Derived from the worker's `job_timeout` (900s) plus a minute for a timing-out
    # job to record its failure, rather than from a job duration. Measurement is
    # what rules the alternative out: a real end-to-end run of the 5-page test
    # lecture took 3.9s for 9 chunks with embedding, ~0.44s per chunk and dominated
    # by OpenAI round trips. A 600-chunk document is then minutes, so any threshold
    # sized from the *observed* job would call healthy long documents dead. Past
    # `job_timeout` the reasoning inverts and needs no extrapolation: arq cancels a
    # job at that point, so a run still claiming `running` afterwards has no one
    # left to finish it.
    #
    # The cost of being right rather than fast: a hard-killed worker is not
    # flagged for ~16 minutes. Acceptable because nothing acts on this. Recovery is
    # arq's, and `stale` is only a hint for slice 4's UI -- computed on read, so it
    # costs nothing until someone looks.
    stale_run_after_seconds: int = 960

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
