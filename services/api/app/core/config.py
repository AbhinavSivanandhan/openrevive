from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    database_url: str

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_sync_database_url() -> str:
    database_url = make_url(get_settings().database_url)

    return database_url.set(
        drivername="postgresql+psycopg"
    ).render_as_string(hide_password=False)
