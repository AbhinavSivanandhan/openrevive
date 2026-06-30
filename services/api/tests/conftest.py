from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.core.config import get_settings


def get_test_database_url() -> URL:
    database_url = make_url(get_settings().database_url)
    database_name = database_url.database or ""

    if not database_name.endswith("_test"):
        raise RuntimeError(
            "Refusing to run tests against a non-test database: "
            f"{database_name!r}. Set DATABASE_URL to a dedicated *_test database."
        )

    return database_url.set(drivername="postgresql+psycopg")


def clear_test_data() -> None:
    engine = create_engine(get_test_database_url())

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE TABLE "
                    "crawl_domain_policies, collections, workspaces "
                    "RESTART IDENTITY CASCADE"
                )
            )
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def isolate_test_database() -> Iterator[None]:
    clear_test_data()
    yield
    clear_test_data()
