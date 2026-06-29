from sqlalchemy.engine import make_url

from app.core.config import (
    build_async_database_url_from_secret_payload,
)


def test_build_async_database_url_from_rds_secret_payload() -> None:
    database_url = build_async_database_url_from_secret_payload(
        {
            "username": "openrevive_app",
            "password": "password/with:special@characters",
        },
        host="cluster.example.internal",
        port=5432,
        database="openrevive",
    )

    parsed = make_url(database_url)

    assert parsed.drivername == "postgresql+asyncpg"
    assert parsed.username == "openrevive_app"
    assert parsed.password == "password/with:special@characters"
    assert parsed.host == "cluster.example.internal"
    assert parsed.port == 5432
    assert parsed.database == "openrevive"
