from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.routers.collections import router as collections_router
from app.core.basic_auth import (
    BasicAuthMiddleware,
    build_basic_auth_config,
)
from app.core.config import get_settings
from app.api.routers.crawl_runs import router as crawl_runs_router
from app.api.routers.workspaces import router as workspaces_router
from app.db.session import check_database, close_database, wait_for_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    await wait_for_database()
    yield
    await close_database()


app = FastAPI(
    title="OpenRevive API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    BasicAuthMiddleware,
    auth_config=build_basic_auth_config(get_settings()),
)

app.include_router(workspaces_router)
app.include_router(collections_router)
app.include_router(crawl_runs_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness() -> dict[str, str]:
    try:
        await check_database()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc

    return {"status": "ready", "database": "connected"}
