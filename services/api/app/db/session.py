import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

MAX_STARTUP_ATTEMPTS = 8
INITIAL_RETRY_DELAY_SECONDS = 0.5
MAX_RETRY_DELAY_SECONDS = 4.0

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
)


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
