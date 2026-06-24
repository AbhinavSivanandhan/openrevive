from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.workspace import Workspace

router = APIRouter(
    prefix="/v1/workspaces",
    tags=["workspaces"],
)


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    session: AsyncSession = Depends(get_db_session),
) -> list[Workspace]:
    result = await session.scalars(
        select(Workspace).order_by(Workspace.created_at.asc())
    )
    return list(result)
