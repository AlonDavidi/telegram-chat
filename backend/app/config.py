"""Backend configuration, loaded from the environment or a .env file."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Repo-root .env (local runs from backend/) or a .env next to the app.
        env_file=("../.env", ".env"),
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    cors_origins: list[str] = ["http://localhost:5173"]
    ws_queue_size: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
