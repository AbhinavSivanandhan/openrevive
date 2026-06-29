import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_database_url

logger = logging.getLogger(__name__)

database_url = get_database_url()

MAX_STARTUP_ATTEMPTS = 8
INITIAL_RETRY_DELAY_SECONDS = 0.5
MAX_RETRY_DELAY_SECONDS = 4.0

database_name = make_url(database_url).database or ""

engine_options: dict[str, object] = {
    "pool_pre_ping": True,
}

if database_name.endswith("_test"):
    engine_options["poolclass"] = NullPool

engine: AsyncEngine = create_async_engine(
    database_url,
    **engine_options,
)

session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def wait_for_database() -> None:
    delay_seconds = INITIAL_RETRY_DELAY_SECONDS

    for attempt in range(1, MAX_STARTUP_ATTEMPTS + 1):
        try:
            await check_database()
            logger.info("Database connection established on attempt %s", attempt)
            return
        except SQLAlchemyError as exc:
            if attempt == MAX_STARTUP_ATTEMPTS:
                logger.exception(
                    "Database unavailable after %s startup attempts",
                    MAX_STARTUP_ATTEMPTS,
                )
                raise RuntimeError("Database unavailable during API startup") from exc

            logger.warning(
                "Database unavailable; retrying in %.1f seconds "
                "(attempt %s/%s)",
                delay_seconds,
                attempt,
                MAX_STARTUP_ATTEMPTS,
            )
            await asyncio.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, MAX_RETRY_DELAY_SECONDS)


async def close_database() -> None:
    await engine.dispose()
