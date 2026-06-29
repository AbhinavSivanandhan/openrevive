from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import boto3
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    database_url: str | None = None
    database_secret_arn: str | None = None
    aws_region: str | None = None

    basic_auth_enabled: bool = False
    basic_auth_username: str | None = None
    basic_auth_password: str | None = None
    basic_auth_username_2: str | None = None
    basic_auth_password_2: str | None = None

    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_region_name: str = "us-east-1"

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _non_blank(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def _read_secret_string(
    payload: Mapping[str, Any],
    field: str,
) -> str:
    value = payload.get(field)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Database secret is missing a non-empty {field!r} field."
        )

    return value


def build_async_database_url_from_secret_payload(
    payload: Mapping[str, Any],
) -> str:
    """
    Build an async SQLAlchemy URL from the JSON shape used by
    RDS-managed Secrets Manager credentials.
    """
    username = _read_secret_string(payload, "username")
    password = _read_secret_string(payload, "password")
    host = _read_secret_string(payload, "host")

    database_value = payload.get("dbname") or payload.get("database")

    if (
        not isinstance(database_value, str)
        or not database_value.strip()
    ):
        raise ValueError(
            "Database secret is missing a non-empty 'dbname' field."
        )

    raw_port = payload.get("port", 5432)

    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Database secret has an invalid 'port' field."
        ) from exc

    if port <= 0:
        raise ValueError(
            "Database secret has a non-positive 'port' field."
        )

    return URL.create(
        drivername="postgresql+asyncpg",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database_value,
    ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_database_url() -> str:
    """
    Resolve one database connection URL.

    Local development and tests use DATABASE_URL. ECS production uses the
    RDS-managed secret through the task role, so the connection string is
    constructed only in process memory.
    """
    settings = get_settings()
    direct_url = _non_blank(settings.database_url)
    secret_arn = _non_blank(settings.database_secret_arn)

    if direct_url and secret_arn:
        raise RuntimeError(
            "Set either DATABASE_URL or DATABASE_SECRET_ARN, not both."
        )

    if direct_url:
        return direct_url

    if not secret_arn:
        raise RuntimeError(
            "Set DATABASE_URL for local mode or "
            "DATABASE_SECRET_ARN for AWS mode."
        )

    region_name = (
        _non_blank(settings.aws_region)
        or settings.s3_region_name
    )

    client = boto3.client(
        "secretsmanager",
        region_name=region_name,
    )
    response = client.get_secret_value(SecretId=secret_arn)
    secret_string = response.get("SecretString")

    if not isinstance(secret_string, str) or not secret_string:
        raise RuntimeError(
            "Database secret did not contain a SecretString value."
        )

    try:
        payload = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Database secret did not contain valid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Database secret JSON must be an object."
        )

    return build_async_database_url_from_secret_payload(payload)


def get_sync_database_url() -> str:
    database_url = make_url(get_database_url())

    return database_url.set(
        drivername="postgresql+psycopg"
    ).render_as_string(hide_password=False)
