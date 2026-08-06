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

    # Comma-separated. A localhost placeholder holds until the first Vercel deploy
    # supplies the real origin.
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
